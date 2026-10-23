"""入稿パイプライン（Intake → Extract → Normalize/Structure → Canonical 出力 → Chunk → QA）。

DB への登録（[6] Index）は副作用があるため別コマンド（sensei index）で行う。

各段の成果物を out/{book_id}/v{version}/ に保存し、manifest.json に
(入力ハッシュ, 段のバージョン, 設定) を記録する。同じ条件での再実行はスキップする。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import chunk, qa, render, structure
from .intake import IntakeResult, intake
from .model import DocTree, RawDoc
from .textnorm import NORMALIZE_VERSION


@dataclass
class IngestOptions:
    out_dir: Path = Path("out")
    extractor: str = "auto"  # auto | epub | pymupdf | docling
    detect_tables: bool = True
    book_id: str | None = None
    version: int = 1
    force: bool = False


@dataclass
class IngestResult:
    doc: DocTree
    chunks: chunk.ChunkSet
    out_dir: Path
    report: dict
    timings: dict = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)


def _extractor_for(src: IntakeResult, opts: IngestOptions) -> tuple[str, str]:
    name = opts.extractor
    if name == "auto":
        name = "epub" if src.source_format == "epub" else "pymupdf"
    if name == "epub":
        from .extract import epub

        return name, epub.EXTRACTOR_VERSION
    if name == "pymupdf":
        from .extract import pdf

        return name, pdf.EXTRACTOR_VERSION
    if name == "docling":
        from .extract import docling_pdf

        return name, docling_pdf.EXTRACTOR_VERSION
    raise ValueError(f"unknown extractor: {name}")


def _run_extract(name: str, src: IntakeResult, assets_dir: Path, opts: IngestOptions) -> RawDoc:
    if name == "epub":
        from .extract.epub import extract_epub

        return extract_epub(src, assets_dir)
    if name == "pymupdf":
        from .extract.pdf import extract_pdf

        return extract_pdf(src, assets_dir, detect_tables=opts.detect_tables)
    from .extract.docling_pdf import extract_docling

    return extract_docling(src, assets_dir)


def _key(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:16]


def ingest(path: str | Path, opts: IngestOptions | None = None) -> IngestResult:
    opts = opts or IngestOptions()
    timings: dict[str, float] = {}
    skipped: list[str] = []

    t = time.perf_counter()
    src = intake(path)
    timings["intake"] = time.perf_counter() - t

    book_id = opts.book_id or src.default_book_id
    ext_name, ext_version = _extractor_for(src, opts)
    out = opts.out_dir / book_id / f"v{opts.version}" / ext_name
    stages_dir = out / "stages"
    stages_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"stages": {}}

    def cached(stage: str, key: str, file: Path) -> bool:
        return not opts.force and file.exists() and manifest["stages"].get(stage, {}).get("key") == key

    # [1] Extract
    extract_key = _key(src.sha256, ext_name, ext_version, NORMALIZE_VERSION, opts.detect_tables)
    raw_path = stages_dir / "extract.json"
    if cached("extract", extract_key, raw_path):
        raw = RawDoc.model_validate_json(raw_path.read_text())
        skipped.append("extract")
    else:
        assets_dir = out / "assets"
        if assets_dir.exists():
            shutil.rmtree(assets_dir)
        t = time.perf_counter()
        raw = _run_extract(ext_name, src, assets_dir, opts)
        timings["extract"] = time.perf_counter() - t
        raw.meta.book_id = book_id
        raw.meta.version = opts.version
        raw_path.write_text(raw.model_dump_json(indent=1))
        manifest["stages"]["extract"] = {"key": extract_key, "version": ext_version, "output": "stages/extract.json"}

    # [2][3] Normalize / Structure → Canonical
    structure_key = _key(extract_key, structure.STRUCTURE_VERSION)
    doc_path = out / "doc.json"
    if cached("structure", structure_key, doc_path):
        doc = DocTree.model_validate_json(doc_path.read_text())
        skipped.append("structure")
    else:
        t = time.perf_counter()
        doc = structure.build_doctree(raw)
        timings["structure"] = time.perf_counter() - t
        doc_path.write_text(doc.model_dump_json(indent=1, exclude_none=True))
        (out / "doc.md").write_text(render.render_markdown(doc))
        manifest["stages"]["structure"] = {"key": structure_key, "version": structure.STRUCTURE_VERSION, "output": "doc.json"}

    # [4] Chunk
    chunk_params = chunk.ChunkParams()
    chunk_key = _key(structure_key, chunk.CHUNK_VERSION, chunk_params.model_dump())
    chunks_path = out / "chunks.json"
    if cached("chunk", chunk_key, chunks_path):
        chunks = chunk.ChunkSet.model_validate_json(chunks_path.read_text())
        skipped.append("chunk")
    else:
        t = time.perf_counter()
        chunks = chunk.chunk_doc(doc, chunk_params)
        timings["chunk"] = time.perf_counter() - t
        chunks_path.write_text(chunks.model_dump_json(indent=1, exclude_none=True))
        manifest["stages"]["chunk"] = {"key": chunk_key, "version": chunk.CHUNK_VERSION, "output": "chunks.json"}

    # [7] QA（毎回実行。軽い）
    report = qa.check(doc, chunks)
    (out / "qa.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))

    manifest.update(
        {
            "book_id": book_id,
            "version": opts.version,
            "source": {"filename": src.path.name, "sha256": src.sha256, "format": src.source_format, "bytes": src.size_bytes},
            "extractor": ext_name,
            "qa_status": report["status"],
        }
    )
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
    return IngestResult(doc=doc, chunks=chunks, out_dir=out, report=report, timings=timings, skipped=skipped)
