"""[6] Index: Canonical とチャンクを PostgreSQL に登録する。"""

from __future__ import annotations

import hashlib
import json
import os
from importlib import resources
from pathlib import Path

import psycopg
from psycopg import sql

from ..chunk import ChunkSet
from ..embed import Embedder, embedding_text
from ..fts import document_text
from ..model import DocTree

DEFAULT_DSN = "postgresql://sensei:sensei@localhost:5432/sensei"


def connect(dsn: str | None = None) -> psycopg.Connection:
    return psycopg.connect(dsn or os.environ.get("SENSEI_DSN", DEFAULT_DSN))


def init_schema(conn: psycopg.Connection) -> None:
    ddl = resources.files("sensei.db").joinpath("schema.sql").read_text()
    with conn.transaction():
        conn.execute(ddl)


def vector_literal(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.7g}" for x in v) + "]"


def _index_name(model: str) -> str:
    return "emb_hnsw_" + hashlib.sha1(model.encode()).hexdigest()[:12]


def ensure_vector_index(conn: psycopg.Connection, model: str, dim: int) -> None:
    """モデルごとの HNSW 部分式索引（検索側も同じ式 embedding::vector(dim) を使うこと）。"""
    conn.execute(
        sql.SQL(
            "CREATE INDEX IF NOT EXISTS {name} ON chunk_embeddings "
            "USING hnsw ((embedding::vector({dim})) vector_cosine_ops) WHERE model = {model}"
        ).format(name=sql.Identifier(_index_name(model)), dim=sql.Literal(dim), model=sql.Literal(model))
    )


def load_outputs(out_dir: Path) -> tuple[DocTree, ChunkSet, dict, dict]:
    doc = DocTree.model_validate_json((out_dir / "doc.json").read_text())
    chunks = ChunkSet.model_validate_json((out_dir / "chunks.json").read_text())
    manifest = json.loads((out_dir / "manifest.json").read_text())
    qa = json.loads((out_dir / "qa.json").read_text()) if (out_dir / "qa.json").exists() else {}
    return doc, chunks, manifest, qa


def index_book(
    conn: psycopg.Connection,
    out_dir: Path,
    embedder: Embedder | None = None,
    activate: bool = True,
) -> dict:
    """入稿の成果物（doc.json / chunks.json）を登録する。同じ (book_id, version) は置き換える。"""
    doc, chunks, manifest, qa = load_outputs(out_dir)
    b = doc.book

    vectors = None
    if embedder is not None:
        vectors = embedder.embed_documents([embedding_text(c.context_header, c.text) for c in chunks.chunks])

    with conn.transaction():
        conn.execute(
            """INSERT INTO books (book_id, title, authors, isbn, language)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (book_id) DO UPDATE SET title = EXCLUDED.title, authors = EXCLUDED.authors,
                 isbn = EXCLUDED.isbn, language = EXCLUDED.language""",
            (b.book_id, b.title, b.authors, b.isbn, b.language),
        )
        conn.execute("DELETE FROM book_versions WHERE book_id = %s AND version = %s", (b.book_id, b.version))
        conn.execute(
            """INSERT INTO book_versions (book_id, version, source_sha256, source_format, source_filename,
                 extractor, canonical_uri, status, stats, qa)
               VALUES (%s, %s, %s, %s, %s, %s, %s, 'indexed', %s, %s)""",
            (b.book_id, b.version, b.source_sha256, b.source_format, b.source_filename,
             manifest.get("extractor"), str((out_dir / "doc.json").resolve()),
             json.dumps(doc.stats, ensure_ascii=False), json.dumps(qa, ensure_ascii=False)),
        )
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO sections VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                [(b.book_id, b.version, s.section_id, s.parent_id, s.level, s.title, s.heading_path, s.order,
                  s.role, s.page_start, s.page_end, s.page_label_start, s.page_label_end, s.href, s.char_count)
                 for s in doc.sections],
            )
            cur.executemany(
                """INSERT INTO blocks VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                [(b.book_id, b.version, x.block_id, x.section_id, x.order, x.type, x.text, x.caption, x.page_label,
                  x.locator.model_dump_json(exclude_none=True)) for x in doc.blocks],
            )
            cur.executemany(
                """INSERT INTO chunks VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                     setweight(to_tsvector('simple', %s), 'A') || setweight(to_tsvector('simple', %s), 'B'))""",
                [(c.chunk_id, c.book_id, c.version, c.section_id, c.ord, c.heading_path, c.role, c.block_ids,
                  c.text, c.context_header, c.page_start, c.page_end, c.page_label_start, c.page_label_end,
                  c.href, c.token_count, c.chunk_strategy,
                  document_text(" ".join(c.heading_path[-2:])), document_text(c.text)) for c in chunks.chunks],
            )
        if vectors is not None:
            _insert_vectors(conn, [c.chunk_id for c in chunks.chunks], vectors, embedder)
        if activate:
            _activate(conn, b.book_id, b.version)

    return {"book_id": b.book_id, "version": b.version, "sections": len(doc.sections), "blocks": len(doc.blocks),
            "chunks": len(chunks.chunks), "embeddings": len(vectors) if vectors else 0,
            "model": embedder.name if embedder else None}


def _insert_vectors(conn, chunk_ids: list[str], vectors: list[list[float]], embedder: Embedder) -> None:
    ensure_vector_index(conn, embedder.name, embedder.dim)
    with conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO chunk_embeddings (chunk_id, model, dim, embedding) VALUES (%s, %s, %s, %s::vector)
               ON CONFLICT (chunk_id, model) DO UPDATE SET dim = EXCLUDED.dim, embedding = EXCLUDED.embedding""",
            [(cid, embedder.name, embedder.dim, vector_literal(v)) for cid, v in zip(chunk_ids, vectors)],
        )


def embed_book(conn: psycopg.Connection, book_id: str, version: int, embedder: Embedder) -> int:
    """登録済みの書籍に、別モデルの埋め込みを追加する（未登録のチャンクだけ）。"""
    rows = conn.execute(
        """SELECT c.chunk_id, c.context_header, c.text FROM chunks c
           WHERE c.book_id = %s AND c.version = %s
             AND NOT EXISTS (SELECT 1 FROM chunk_embeddings e WHERE e.chunk_id = c.chunk_id AND e.model = %s)
           ORDER BY c.ord""",
        (book_id, version, embedder.name),
    ).fetchall()
    if not rows:
        return 0
    vectors = embedder.embed_documents([embedding_text(h, t) for _, h, t in rows])
    with conn.transaction():
        _insert_vectors(conn, [r[0] for r in rows], vectors, embedder)
    return len(rows)


def _activate(conn, book_id: str, version: int) -> None:
    conn.execute("UPDATE book_versions SET status = 'archived' WHERE book_id = %s AND version <> %s AND status = 'active'",
                 (book_id, version))
    conn.execute("UPDATE book_versions SET status = 'active' WHERE book_id = %s AND version = %s", (book_id, version))
    conn.execute("UPDATE books SET active_version = %s WHERE book_id = %s", (version, book_id))
