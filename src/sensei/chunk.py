"""[4] Chunk: DocTree → 検索単位のチャンク。

- 節（section）の境界をまたがない。同じ節に直接属するブロックだけを束ねる
- 表・コード・箇条書きは途中で切らない（極端に長いものだけ行単位で分割）
- 目次・索引（role=toc/index）は対象外
- 各チャンクに見出しの経路（heading_path）とページ範囲を持たせ、引用表示に使う
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .model import Block, DocTree, Section
from .textnorm import is_cjk

CHUNK_VERSION = "0.1.0"

SKIP_ROLES = {"toc", "index"}


class ChunkParams(BaseModel):
    target_tokens: int = 500
    max_tokens: int = 800
    min_tokens: int = 120  # これ未満の末尾チャンクは直前に併合する


class Chunk(BaseModel):
    chunk_id: str
    book_id: str
    version: int
    section_id: str
    ord: int
    heading_path: list[str]
    role: str
    block_ids: list[str]
    text: str
    context_header: str
    page_start: int | None = None
    page_end: int | None = None
    page_label_start: str | None = None
    page_label_end: str | None = None
    href: str | None = None
    token_count: int
    chunk_strategy: str


class ChunkSet(BaseModel):
    book_id: str
    version: int
    chunk_version: str = CHUNK_VERSION
    params: ChunkParams
    chunks: list[Chunk] = Field(default_factory=list)


def estimate_tokens(text: str) -> int:
    """トークン数の概算。和文は 1 文字 ≒ 1 トークン、それ以外は 4 文字 ≒ 1 トークン。"""
    cjk = sum(1 for c in text if is_cjk(c))
    return cjk + (len(text) - cjk + 3) // 4


def _block_text(b: Block) -> str:
    if b.type == "code":
        head = f"{b.caption}\n" if b.caption else ""
        return head + b.text
    if b.type == "list":
        return "\n".join("- " + i.strip() for i in (b.items or b.text.split("\n")))
    if b.type in ("figure", "table") and b.caption and b.caption not in b.text:
        return f"{b.caption}\n{b.text}" if b.text else b.caption
    if b.type == "note" and b.caption:
        return f"{b.caption}: {b.text}"
    return b.text


def _split_long(b: Block, text: str, max_tokens: int) -> list[str]:
    """max_tokens を超えるブロックを分割する（コード・表は行単位、本文は文単位）。"""
    if estimate_tokens(text) <= max_tokens:
        return [text]
    if b.type in ("code", "table", "list"):
        units = text.split("\n")
        sep = "\n"
    else:
        import re

        units = [u for u in re.split(r"(?<=[。．！？!?])|(?<=\.)\s+", text) if u]
        sep = ""
    parts, cur = [], []
    for u in units:
        if cur and estimate_tokens(sep.join(cur + [u])) > max_tokens:
            parts.append(sep.join(cur))
            cur = []
        cur.append(u)
    if cur:
        parts.append(sep.join(cur))
    return parts


def chunk_doc(doc: DocTree, params: ChunkParams | None = None) -> ChunkSet:
    params = params or ChunkParams()
    sections: dict[str, Section] = {s.section_id: s for s in doc.sections}
    strategy = f"struct-v{CHUNK_VERSION}-{params.target_tokens}t"
    title = doc.book.title or doc.book.source_filename

    # 節ごとにブロックを集める（見出しブロック自体は heading_path で表すので本文に入れない）
    by_section: dict[str, list[Block]] = {}
    order: list[str] = []
    for b in doc.blocks:
        if b.type == "heading":
            continue
        if b.section_id not in by_section:
            by_section[b.section_id] = []
            order.append(b.section_id)
        by_section[b.section_id].append(b)

    chunks: list[Chunk] = []
    for sid in order:
        sec = sections[sid]
        if sec.role in SKIP_ROLES:
            continue
        pieces: list[tuple[Block, str]] = []
        for b in by_section[sid]:
            text = _block_text(b).strip()
            if not text:
                continue
            for part in _split_long(b, text, params.max_tokens):
                pieces.append((b, part))

        groups: list[list[tuple[Block, str]]] = []
        cur: list[tuple[Block, str]] = []
        cur_tokens = 0
        for b, text in pieces:
            t = estimate_tokens(text)
            if cur and (cur_tokens + t > params.max_tokens or cur_tokens >= params.target_tokens):
                groups.append(cur)
                cur, cur_tokens = [], 0
            cur.append((b, text))
            cur_tokens += t
        if cur:
            if groups and cur_tokens < params.min_tokens and (
                sum(estimate_tokens(x) for _, x in groups[-1]) + cur_tokens <= params.max_tokens
            ):
                groups[-1].extend(cur)
            else:
                groups.append(cur)

        for g in groups:
            blocks = [b for b, _ in g]
            text = "\n\n".join(x for _, x in g)
            pages = [loc.page for b in blocks for loc in (b.locator.pdf or [])]
            labels = [b.page_label for b in blocks if b.page_label]
            ord_ = len(chunks)
            chunks.append(
                Chunk(
                    chunk_id=f"{doc.book.book_id}:v{doc.book.version}:c{ord_:05d}",
                    book_id=doc.book.book_id,
                    version=doc.book.version,
                    section_id=sid,
                    ord=ord_,
                    heading_path=sec.heading_path,
                    role=sec.role,
                    block_ids=list(dict.fromkeys(b.block_id for b in blocks)),
                    text=text,
                    # LLM による文脈説明（Contextual Retrieval）は Enrich 段で追記する
                    context_header=" > ".join([title, *sec.heading_path]),
                    page_start=min(pages) if pages else None,
                    page_end=max(pages) if pages else None,
                    page_label_start=labels[0] if labels else None,
                    page_label_end=labels[-1] if labels else None,
                    href=blocks[0].locator.epub.href if blocks[0].locator.epub else None,
                    token_count=estimate_tokens(text),
                    chunk_strategy=strategy,
                )
            )
    return ChunkSet(book_id=doc.book.book_id, version=doc.book.version, params=params, chunks=chunks)
