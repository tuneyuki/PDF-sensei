"""[2]+[3] Normalize / Structure: RawDoc → DocTree。

- 見出しブロックから章・節の木を作り、全ブロックに section_id を付与
- 目次・索引・前付けなどの役割（role）を判定（後段でチャンク対象から外す等に使う）
- 目次（nav / しおり）と節の突き合わせ
- 安定 ID の採番と統計
"""

from __future__ import annotations

import re

from .model import Block, DocTree, RawDoc, Section
from .textnorm import search_key

STRUCTURE_VERSION = "0.1.1"

TOC_TITLES = {"目次", "contents", "tableofcontents", "もくじ"}
INDEX_TITLES = {"索引", "index", "さくいん"}
FRONT_TITLES = re.compile(
    r"(まえがき|はじめに|序文|序章|前書き|謝辞|献辞|ライセンス|著者紹介|"
    r"^preface|^foreword|^introduction|acknowledg|dedication|^license|copyright|about the author|contributors|^untitled)",
    re.I,
)
BACK_TITLES = re.compile(r"^(あとがき|おわりに|付録|参考文献|appendix|afterword|bibliography|references|glossary|用語集)", re.I)
EPUB_ROLE = {
    "toc": "toc", "index": "index", "preface": "front", "foreword": "front", "frontmatter": "front",
    "dedication": "front", "acknowledgments": "front", "copyright-page": "front", "titlepage": "front",
    "appendix": "back", "backmatter": "back", "afterword": "back", "bibliography": "back", "glossary": "back",
}


def build_doctree(raw: RawDoc) -> DocTree:
    blocks = [b.model_copy(deep=True) for b in raw.blocks]
    _normalize_levels(blocks)

    sections: list[Section] = []
    stack: list[Section] = []
    seen_body = False
    current: Section | None = None

    for i, b in enumerate(blocks):
        b.order = i
        b.block_id = f"b{i:06d}"
        if b.type == "heading":
            level = b.level or 1
            while stack and stack[-1].level >= level:
                stack.pop()
            parent = stack[-1] if stack else None
            role = _role(b, parent, seen_body, raw.meta.title)
            if role == "body" and level == 1:
                seen_body = True
            current = Section(
                section_id=f"s{len(sections) + 1:04d}",
                parent_id=parent.section_id if parent else None,
                level=level,
                title=b.text,
                heading_path=[*(parent.heading_path if parent else []), b.text],
                order=len(sections),
                role=role,
                href=b.locator.epub.href if b.locator.epub else None,
            )
            sections.append(current)
            stack.append(current)
        elif current is None:
            # 最初の見出しより前のブロックは「前付け」節に入れる
            current = Section(
                section_id="s0000", parent_id=None, level=1, title=raw.meta.title or "(前付け)",
                heading_path=[raw.meta.title or "(前付け)"], order=-1, role="front",
            )
            sections.insert(0, current)
            stack.append(current)
        b.section_id = current.section_id

    _fill_section_stats(sections, blocks)
    toc = [e.model_copy() for e in raw.toc]
    matched = _match_toc(toc, sections)

    stats = _stats(raw, blocks, sections, matched)
    return DocTree(
        book=raw.meta,
        toc=toc,
        sections=sections,
        blocks=blocks,
        assets=raw.assets,
        stats=stats,
    )


def _normalize_levels(blocks: list[Block]) -> None:
    """見出しレベルを 1 始まり・飛びなしに詰める（h2 から始まる EPUB や h2→h4 の飛びを補正）。"""
    used = sorted({b.level for b in blocks if b.type == "heading" and b.level})
    remap = {lvl: i + 1 for i, lvl in enumerate(used)}
    for b in blocks:
        if b.type == "heading" and b.level:
            b.level = remap[b.level]


def _role(b: Block, parent: Section | None, seen_body: bool, book_title: str | None = None) -> str:
    if parent is not None and parent.role in ("toc", "index", "front", "back"):
        return parent.role
    key = search_key(b.text)
    if not seen_body and book_title and key == search_key(book_title):
        return "front"  # 扉の書名
    if key in TOC_TITLES:
        return "toc"
    if key in INDEX_TITLES:
        return "index"
    etype = (b.annotations or {}).get("epub_type", "")
    for t in etype.split():
        if t in EPUB_ROLE:
            return EPUB_ROLE[t]
    if FRONT_TITLES.search(b.text) and not seen_body:
        return "front"
    if BACK_TITLES.match(b.text) and seen_body:
        return "back"
    if not seen_body and (b.level or 1) == 1 and "synthetic" in b.flags:
        return "front"
    return "body"


def _fill_section_stats(sections: list[Section], blocks: list[Block]) -> None:
    by_id = {s.section_id: s for s in sections}
    parent_of = {s.section_id: s.parent_id for s in sections}
    for b in blocks:
        sid = b.section_id
        own = True
        while sid:
            s = by_id[sid]
            if own:
                s.block_count += 1
            s.char_count += len(b.text)
            pages = [l.page for l in (b.locator.pdf or [])]
            if pages:
                if s.page_start is None or min(pages) < s.page_start:
                    s.page_start = min(pages)
                    s.page_label_start = b.page_label or s.page_label_start
                if s.page_end is None or max(pages) > s.page_end:
                    s.page_end = max(pages)
                    s.page_label_end = b.page_label or s.page_label_end
            sid = parent_of[sid]
            own = False


def _match_toc(toc, sections: list[Section]) -> int:
    """目次項目を節に対応付ける（タイトル一致・出現順）。一致数を返す。"""
    keys = [(search_key(s.title), s) for s in sections]
    pos = 0
    matched = 0
    for e in toc:
        k = search_key(e.title)
        for j in range(pos, len(keys)):
            if keys[j][0] == k:
                e.section_id = keys[j][1].section_id
                pos = j + 1
                matched += 1
                break
    return matched


def _stats(raw: RawDoc, blocks: list[Block], sections: list[Section], toc_matched: int) -> dict:
    from collections import Counter

    types = Counter(b.type for b in blocks)
    chars = Counter()
    for b in blocks:
        chars[b.type] += len(b.text)
    body_chars = sum(len(b.text) for b, s in _with_section(blocks, sections) if s.role == "body")
    text = "".join(b.text for b in blocks)
    return {
        "structure_version": STRUCTURE_VERSION,
        "blocks": len(blocks),
        "blocks_by_type": dict(types),
        "chars_total": sum(chars.values()),
        "chars_by_type": dict(chars),
        "chars_body_role": body_chars,
        "sections": len(sections),
        "sections_by_level": dict(Counter(s.level for s in sections)),
        "sections_by_role": dict(Counter(s.role for s in sections)),
        "toc_entries": len(raw.toc),
        "toc_matched": toc_matched,
        "assets": len(raw.assets),
        "replacement_char_ratio": round(text.count("�") / max(1, len(text)), 6),
        "warnings": raw.warnings,
    }


def _with_section(blocks, sections):
    by_id = {s.section_id: s for s in sections}
    for b in blocks:
        yield b, by_id[b.section_id]
