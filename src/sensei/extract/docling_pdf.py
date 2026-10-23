"""[1] Extract: PDF → RawDoc（Docling のレイアウト解析モデルを使う抽出器）。

Docling は読み順・見出し・表・図・柱の判定を学習済みモデルで行う。見出しの階層は出さない
（ほぼ全て同じレベルになる）ため、PDF のしおりと照合してレベルを付ける。
pip install 'pdf-sensei[docling]' が必要（torch を含み重い）。
"""

from __future__ import annotations

from pathlib import Path

from ..intake import IntakeResult
from ..model import Asset, Block, BookMeta, Extraction, Locator, PdfLocator, RawDoc, TocEntry
from ..textnorm import is_cjk, normalize_text, search_key

EXTRACTOR_VERSION = "0.1.0"


def extract_docling(src: IntakeResult, assets_dir: Path | None = None, page_range: tuple[int, int] | None = None) -> RawDoc:
    import importlib.metadata

    import pymupdf
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling_core.types.doc import DocItemLabel

    with pymupdf.open(src.path) as pdoc:
        toc = [TocEntry(level=lvl, title=normalize_text(t), page=p) for lvl, t, p in pdoc.get_toc(simple=True)]
        meta_raw = pdoc.metadata or {}
        page_count = pdoc.page_count
        page_heights = [p.rect.height for p in pdoc]

    opts = PdfPipelineOptions()
    opts.do_ocr = False  # テキスト PDF 前提。スキャン PDF は do_ocr=True（日本語は OCR エンジン設定が必要）
    opts.do_table_structure = True
    opts.generate_picture_images = assets_dir is not None
    opts.images_scale = 1.5
    conv = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})
    kwargs = {"page_range": page_range} if page_range else {}
    ddoc = conv.convert(str(src.path), **kwargs).document

    toc_level = {}
    for e in toc:
        toc_level.setdefault(search_key(e.title), e.level)
    deepest = max((e.level for e in toc), default=1)

    blocks: list[Block] = []
    assets: list[Asset] = []
    list_buf: list[tuple[str, Locator]] = []

    def flush_list():
        if list_buf:
            items = [t for t, _ in list_buf]
            pdf_locs = [loc for _, l in list_buf for loc in (l.pdf or [])]
            blocks.append(Block(type="list", text="\n".join(items), items=items, locator=Locator(pdf=pdf_locs)))
            list_buf.clear()

    skip = {DocItemLabel.PAGE_HEADER, DocItemLabel.PAGE_FOOTER, DocItemLabel.CAPTION}
    for item, _depth in ddoc.iterate_items():
        label = getattr(item, "label", None)
        if label is None or label in skip:
            continue
        loc = _locator(item, page_heights)
        text = normalize_text(getattr(item, "text", "") or "")
        if label != DocItemLabel.LIST_ITEM:
            flush_list()
        if label in (DocItemLabel.TITLE, DocItemLabel.SECTION_HEADER):
            level = toc_level.get(search_key(text), deepest + 1)
            blocks.append(Block(type="heading", text=text, level=level, locator=loc))
        elif label == DocItemLabel.LIST_ITEM:
            list_buf.append((text, loc))
        elif label == DocItemLabel.CODE:
            blocks.append(Block(type="code", text=getattr(item, "text", "") or "", locator=loc, caption=_caption(item, ddoc)))
        elif label == DocItemLabel.TABLE:
            blocks.append(Block(type="table", text=item.export_to_markdown(doc=ddoc), caption=_caption(item, ddoc), locator=loc))
        elif label in (DocItemLabel.PICTURE, DocItemLabel.CHART):
            cap = _caption(item, ddoc)
            asset_id = None
            if assets_dir is not None:
                img = item.get_image(ddoc)
                if img is not None:
                    asset_id = f"img-{len(assets) + 1:04d}"
                    assets_dir.mkdir(parents=True, exist_ok=True)
                    img.save(assets_dir / f"{asset_id}.png")
                    assets.append(Asset(asset_id=asset_id, path=f"assets/{asset_id}.png", media_type="image/png", caption=cap))
            blocks.append(Block(type="figure", text=cap or "", caption=cap, asset_id=asset_id, locator=loc))
        elif label == DocItemLabel.FOOTNOTE:
            blocks.append(Block(type="footnote", text=text, locator=loc))
        elif label == DocItemLabel.FORMULA:
            blocks.append(Block(type="equation", text=text, locator=loc))
        elif label == DocItemLabel.DOCUMENT_INDEX:
            blocks.append(Block(type="table", text=item.export_to_markdown(doc=ddoc) if hasattr(item, "export_to_markdown") else text,
                                locator=loc, flags=["document_index"]))
        elif text:
            blocks.append(Block(type="paragraph", text=text, locator=loc))
    flush_list()

    sample = "".join(b.text for b in blocks[:400])
    meta = BookMeta(
        book_id=src.default_book_id,
        source_sha256=src.sha256,
        source_format="pdf",
        source_filename=src.path.name,
        title=normalize_text(meta_raw.get("title") or "") or None,
        authors=[a.strip() for a in (meta_raw.get("author") or "").split(",") if a.strip()],
        language="ja" if sum(is_cjk(c) for c in sample) / max(1, len(sample)) > 0.1 else "en",
        page_count=page_count,
        extraction=Extraction(
            extractor="docling",
            extractor_version=EXTRACTOR_VERSION,
            params={"docling": importlib.metadata.version("docling"), "do_ocr": False, "page_range": page_range},
        ),
    )
    return RawDoc(meta=meta, toc=toc, blocks=blocks, assets=assets)


def _caption(item, ddoc) -> str | None:
    try:
        cap = item.caption_text(ddoc)
    except Exception:  # noqa: BLE001
        return None
    return normalize_text(cap) or None


def _locator(item, page_heights) -> Locator:
    locs = []
    for prov in getattr(item, "prov", []) or []:
        h = page_heights[prov.page_no - 1]
        bb = prov.bbox.to_top_left_origin(page_height=h)
        locs.append(PdfLocator(page=prov.page_no, bbox=(round(bb.l, 1), round(bb.t, 1), round(bb.r, 1), round(bb.b, 1))))
    return Locator(pdf=locs or None)
