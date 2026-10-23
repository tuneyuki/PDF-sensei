"""ハイブリッド検索（全文検索 + ベクトル検索を RRF で統合）。

検索対象は各書籍の公開中バージョン（books.active_version）のチャンク。
返す結果には、出典表示に必要な書名・見出しの経路・ページを含める。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import psycopg
from psycopg import sql

from .db.store import vector_literal
from .embed import Embedder
from .fts import query_expr

RRF_K = 60
CANDIDATES = 50


@dataclass
class Hit:
    chunk_id: str
    book_id: str
    version: int
    section_id: str
    title: str | None
    heading_path: list[str]
    text: str
    block_ids: list[str]
    page_start: int | None
    page_end: int | None
    page_label_start: str | None
    page_label_end: str | None
    score: float
    ranks: dict = field(default_factory=dict)  # {"fts": 3, "vector": 1}

    @property
    def citation(self) -> str:
        where = " > ".join(self.heading_path)
        if self.page_label_start:
            pages = self.page_label_start if self.page_label_start == self.page_label_end or not self.page_label_end \
                else f"{self.page_label_start}-{self.page_label_end}"
            where += f"（p.{pages}）"
        elif self.page_start:
            where += f"（PDF {self.page_start} ページ）"
        return f"『{self.title}』{where}"


def _filters(book_ids: list[str] | None) -> tuple[sql.Composable, list]:
    if book_ids:
        return sql.SQL(" AND c.book_id = ANY(%s)"), [book_ids]
    return sql.SQL(""), []


def fts_candidates(conn, query: str, book_ids=None, limit=CANDIDATES) -> list[tuple[str, float]]:
    expr = query_expr(query)
    if not expr:
        return []
    where, params = _filters(book_ids)
    q = sql.SQL(
        """SELECT c.chunk_id, ts_rank(c.fts, q) AS score
           FROM chunks c JOIN books b ON b.book_id = c.book_id AND b.active_version = c.version,
                to_tsquery('simple', %s) q
           WHERE c.fts @@ q{where}
           ORDER BY score DESC LIMIT %s"""
    ).format(where=where)
    return [(r[0], float(r[1])) for r in conn.execute(q, [expr, *params, limit]).fetchall()]


def vector_candidates(conn, query: str, embedder: Embedder, book_ids=None, limit=CANDIDATES) -> list[tuple[str, float]]:
    vec = vector_literal(embedder.embed_query(query))
    where, params = _filters(book_ids)
    dim = sql.Literal(embedder.dim)
    q = sql.SQL(
        """SELECT e.chunk_id, 1 - (e.embedding::vector({dim}) <=> %s::vector({dim})) AS score
           FROM chunk_embeddings e
           JOIN chunks c ON c.chunk_id = e.chunk_id
           JOIN books b ON b.book_id = c.book_id AND b.active_version = c.version
           WHERE e.model = %s{where}
           ORDER BY e.embedding::vector({dim}) <=> %s::vector({dim}) LIMIT %s"""
    ).format(dim=dim, where=where)
    return [(r[0], float(r[1])) for r in conn.execute(q, [vec, embedder.name, *params, vec, limit]).fetchall()]


def search(
    conn: psycopg.Connection,
    query: str,
    k: int = 10,
    mode: str = "hybrid",  # hybrid | fts | vector
    embedder: Embedder | None = None,
    book_ids: list[str] | None = None,
) -> list[Hit]:
    lists: dict[str, list[tuple[str, float]]] = {}
    if mode in ("hybrid", "fts"):
        lists["fts"] = fts_candidates(conn, query, book_ids)
    if mode in ("hybrid", "vector"):
        if embedder is None:
            if mode == "vector":
                raise ValueError("vector 検索には embedder が必要です")
        else:
            lists["vector"] = vector_candidates(conn, query, embedder, book_ids)

    fused: dict[str, float] = {}
    ranks: dict[str, dict] = {}
    for name, items in lists.items():
        for rank, (cid, _score) in enumerate(items, start=1):
            fused[cid] = fused.get(cid, 0.0) + 1.0 / (RRF_K + rank)
            ranks.setdefault(cid, {})[name] = rank
    top = sorted(fused.items(), key=lambda x: -x[1])[:k]
    if not top:
        return []

    rows = conn.execute(
        """SELECT c.chunk_id, c.book_id, c.version, c.section_id, b.title, c.heading_path, c.text, c.block_ids,
                  c.page_start, c.page_end, c.page_label_start, c.page_label_end
           FROM chunks c JOIN books b ON b.book_id = c.book_id WHERE c.chunk_id = ANY(%s)""",
        ([cid for cid, _ in top],),
    ).fetchall()
    by_id = {r[0]: r for r in rows}
    hits = []
    for cid, score in top:
        r = by_id[cid]
        hits.append(Hit(*r, score=score, ranks=ranks[cid]))
    return hits
