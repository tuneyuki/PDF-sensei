"""[1] Extract: テキスト PDF → RawDoc（PyMuPDF によるヒューリスティック抽出）。

方針:
- 行単位でフォント・サイズ・位置を取り、本文サイズを基準に 見出し/コード/キャプション/箇条書き/本文 を分類
- 余白にあり複数ページで繰り返す行は柱・ノンブルとして除去（ノンブルは page_label に使う）
- 見出しレベルは PDF のしおり（outline）と照合してフォントサイズ→レベルの対応を学習する
- 段落はページをまたいでも連結する

前提: 1 段組・横書き。多段組・縦書き・スキャン PDF は別の抽出器（Docling / OCR / LLM ビジョン）で扱う。
"""

from __future__ import annotations

import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from ..intake import IntakeResult
from ..model import Asset, Block, BookMeta, Extraction, Locator, PdfLocator, RawDoc, TocEntry
from ..textnorm import is_cjk, join_lines, normalize_text, search_key

EXTRACTOR_VERSION = "0.3.3"

MONO_FONT_RE = re.compile(
    r"mono|courier|consol|menlo|monaco|inconsolata|source ?code|code|fixed|typewriter|\dmn\b|\dmn-|^cmtt|lmmono",
    re.I,
)
ICON_FONT_RE = re.compile(r"awesome|icon|symbol|dingbat|wingding|webding|zapf", re.I)
PUA_RE = re.compile("[\ue000-\uf8ff]")  # 私用領域（アイコンフォントのグリフ）
CAPTION_RE = re.compile(r"^(図|表|例|リスト|Figure|Fig\.|Table|Example|Listing)\s*\d+(\.\d+)*[\.:：．]?\s")
BULLET_RE = re.compile(r"^([•●◦▪■□・‣◆◇①-⑳]|[\-–—*](?=\s))\s*")
WEAK_BULLETS = set("-–—*")  # 行頭に来やすい記号（折り返された "--list" 等）。字下げ時のみ箇条書きとみなす
ORDERED_RE = re.compile(r"^(\d{1,2}|[a-z]|[ivx]{1,4})[\.\)]\s+")
SENTENCE_END = tuple("。．.!?！？」』）):：")
PAGE_NUM_RE = re.compile(r"^(\d{1,4}|[ivxlcdm]{1,6})$", re.I)


@dataclass
class Line:
    page: int  # 1 始まり
    bbox: tuple[float, float, float, float]
    text: str
    size: float
    font: str
    mono: bool
    bold: bool
    first_mono: bool = False  # 行頭が等幅（「`*` の前に…」のような行を箇条書きと誤認しないため）
    kind: str = ""  # furniture|heading|code|caption|bullet|body

    @property
    def x0(self):
        return self.bbox[0]

    @property
    def y0(self):
        return self.bbox[1]

    @property
    def y1(self):
        return self.bbox[3]


@dataclass
class Item:
    """ページ内の要素（テキスト行 / 画像 / 表）。"""

    page: int
    bbox: tuple[float, float, float, float]
    line: Line | None = None
    image: bool = False
    table_md: str | None = None


@dataclass
class _Group:
    type: str
    lines: list[Line] = field(default_factory=list)
    items: list[list[Line]] = field(default_factory=list)  # list 用
    bbox_by_page: dict = field(default_factory=dict)
    image_bbox: tuple | None = None
    image_page: int | None = None
    table_md: str | None = None
    caption: str | None = None
    level: int | None = None

    def add_bbox(self, page, bbox):
        cur = self.bbox_by_page.get(page)
        self.bbox_by_page[page] = bbox if cur is None else (
            min(cur[0], bbox[0]), min(cur[1], bbox[1]), max(cur[2], bbox[2]), max(cur[3], bbox[3])
        )


def extract_pdf(src: IntakeResult, assets_dir: Path | None = None, detect_tables: bool = True) -> RawDoc:
    doc = pymupdf.open(src.path)
    warnings: list[str] = []
    try:
        lines_by_page, images_by_page = _read_pages(doc)
        all_lines = [ln for ls in lines_by_page.values() for ln in ls]
        if not all_lines:
            raise ValueError("テキスト層がありません（スキャン PDF の可能性）。OCR 抽出器を使ってください。")

        body_size, body_left = _body_metrics(all_lines)
        code_size, code_left = _code_metrics(all_lines, body_left)
        page_labels = _mark_furniture(lines_by_page, doc)
        empty_pages = [p for p in range(1, doc.page_count + 1) if not any(l.kind != "furniture" for l in lines_by_page.get(p, []))]

        toc = [TocEntry(level=lvl, title=normalize_text(title), page=page) for lvl, title, page in doc.get_toc(simple=True)]
        for ln in all_lines:
            if ln.kind != "furniture":
                ln.kind = _classify(ln, body_size, body_left, code_size, code_left)
        level_of_size = _heading_levels(lines_by_page, toc, body_size)

        tables_by_page = _find_tables(doc, lines_by_page) if detect_tables else {}

        groups: list[_Group] = []
        for pno in range(1, doc.page_count + 1):
            items = _page_items(pno, lines_by_page.get(pno, []), images_by_page.get(pno, []), tables_by_page.get(pno, []))
            groups.extend(_group_page(items, body_size, body_left, level_of_size, groups))

        vocab = _vocabulary(all_lines)
        minor_level = max(level_of_size.values(), default=0) + 1
        groups = _merge_across_pages(groups, body_left)
        _mark_minor_headings(groups, minor_level)
        blocks, assets = _to_blocks(groups, doc, page_labels, body_left, assets_dir, vocab)
    finally:
        meta_raw = doc.metadata or {}
        page_count = doc.page_count
        doc.close()

    sample = "".join(b.text for b in blocks[:400])
    cjk_ratio = sum(is_cjk(c) for c in sample) / max(1, len(sample))
    meta = BookMeta(
        book_id=src.default_book_id,
        source_sha256=src.sha256,
        source_format="pdf",
        source_filename=src.path.name,
        title=normalize_text(meta_raw.get("title") or "") or None,
        authors=[a.strip() for a in re.split(r"[,、;]| and ", meta_raw.get("author") or "") if a.strip()],
        language="ja" if cjk_ratio > 0.1 else "en",
        page_count=page_count,
        extraction=Extraction(
            extractor="pymupdf-heuristic",
            extractor_version=EXTRACTOR_VERSION,
            params={
                "pymupdf": pymupdf.VersionBind,
                "body_size": body_size,
                "body_left": body_left,
                "heading_sizes": {str(k): v for k, v in sorted(level_of_size.items(), reverse=True)},
                "detect_tables": detect_tables,
            },
        ),
    )
    if empty_pages:
        warnings.append(f"本文テキストのないページ: {len(empty_pages)} ページ（例: {empty_pages[:10]}）")
    return RawDoc(meta=meta, toc=toc, blocks=blocks, assets=assets, warnings=warnings)


# --- ページ読み取り ----------------------------------------------------------


def _read_pages(doc) -> tuple[dict[int, list[Line]], dict[int, list[tuple]]]:
    lines_by_page: dict[int, list[Line]] = {}
    images_by_page: dict[int, list[tuple]] = {}
    for page in doc:
        pno = page.number + 1
        d = page.get_text("dict")
        lines, images = [], []
        for b in d["blocks"]:
            if b["type"] == 1:
                x0, y0, x1, y1 = b["bbox"]
                if (x1 - x0) >= 30 and (y1 - y0) >= 30:  # アイコン等の小画像は除外
                    images.append(tuple(b["bbox"]))
                continue
            for l in b["lines"]:
                if l.get("dir", (1, 0))[0] < 0.9:  # 回転テキストは対象外
                    continue
                spans = [
                    {**s, "text": PUA_RE.sub("", s["text"])}
                    for s in l["spans"]
                    if s["size"] >= 4 and s["text"] and not ICON_FONT_RE.search(s["font"])
                ]
                spans = [s for s in spans if s["text"]]
                text = "".join(s["text"] for s in spans)
                if not text.strip():
                    continue
                weight = Counter()
                for s in spans:
                    weight[(round(s["size"], 1), s["font"])] += len(s["text"].strip()) or 1
                (size, font), _ = weight.most_common(1)[0]
                visible = [s for s in spans if s["text"].strip()]
                mono = all(MONO_FONT_RE.search(s["font"]) or s["flags"] & 8 for s in visible)
                bold = all("bold" in s["font"].lower() or s["flags"] & 16 for s in visible)
                first_mono = bool(visible and (MONO_FONT_RE.search(visible[0]["font"]) or visible[0]["flags"] & 8))
                lines.append(Line(pno, tuple(l["bbox"]), text.rstrip(), size, font, bool(mono), bool(bold), first_mono))
        lines_by_page[pno] = lines
        images_by_page[pno] = images
    return lines_by_page, images_by_page


def _body_metrics(lines: list[Line]) -> tuple[float, float]:
    sizes = Counter()
    lefts = Counter()
    for ln in lines:
        if not ln.mono:
            sizes[ln.size] += len(ln.text)
    body_size = sizes.most_common(1)[0][0]
    for ln in lines:
        if not ln.mono and ln.size == body_size:
            lefts[round(ln.x0)] += 1
    return body_size, float(lefts.most_common(1)[0][0])


def _code_metrics(lines: list[Line], body_left: float) -> tuple[float | None, float | None]:
    """コードブロックの典型サイズと左端。和文が混じるコード行（等幅でない和文フォント）の判定に使う。"""
    sizes, lefts = Counter(), Counter()
    for ln in lines:
        if ln.mono and ln.x0 > body_left + 3:
            sizes[ln.size] += 1
            lefts[round(ln.x0)] += 1
    if not sizes:
        return None, None
    return sizes.most_common(1)[0][0], float(lefts.most_common(1)[0][0])


def _mark_furniture(lines_by_page: dict[int, list[Line]], doc) -> dict[int, str]:
    """柱・ノンブルを furniture にし、ページ番号ラベルを返す。"""
    key_pages: dict[str, set[int]] = defaultdict(set)
    margin_lines = []
    for pno, lines in lines_by_page.items():
        h = doc[pno - 1].rect.height
        for ln in lines:
            if ln.y1 < h * 0.08 or ln.y0 > h * 0.92:
                margin_lines.append(ln)
                key_pages[re.sub(r"\d+", "#", search_key(ln.text))].add(pno)
    labels: dict[int, str] = {}
    for ln in margin_lines:
        txt = ln.text.strip()
        if PAGE_NUM_RE.match(txt):
            ln.kind = "furniture"
            labels.setdefault(ln.page, txt)
        elif len(key_pages[re.sub(r"\d+", "#", search_key(ln.text))]) >= 3:
            ln.kind = "furniture"
            m = re.search(r"(?:^|\s|\|)(\d{1,4})$|^(\d{1,4})(?:\s|\|)", txt)
            if m:
                labels.setdefault(ln.page, m.group(1) or m.group(2))
    return labels


def _classify(ln: Line, body_size: float, body_left: float, code_size: float | None, code_left: float | None) -> str:
    text = ln.text.strip()
    if CAPTION_RE.match(text) and not ln.mono:
        return "caption"
    if ln.mono and (ln.x0 > body_left + 3 or abs(ln.size - body_size) > 0.2):
        return "code"
    if (
        code_size is not None
        and abs(code_size - body_size) > 0.2
        and abs(ln.size - code_size) < 0.05
        and ln.x0 >= code_left - 1
    ):
        return "code"  # コード中の和文（和文フォントで描画されるため等幅判定に掛からない）
    if not ln.mono and ln.size >= body_size * 1.15 and len(text) <= 120:
        return "heading"
    m = BULLET_RE.match(text)
    if m and not ln.first_mono and (text[0] not in WEAK_BULLETS or ln.x0 > body_left + 3):
        return "bullet"
    if ORDERED_RE.match(text) and ln.x0 > body_left + 3:
        return "bullet"  # 字下げされた「1. 」「a) 」は番号付き箇条書き
    return "body"


def _heading_levels(lines_by_page, toc: list[TocEntry], body_size: float) -> dict[float, int]:
    """しおりのタイトルと一致する見出し行のフォントサイズから サイズ→レベル を学習する。"""
    votes: dict[float, Counter] = defaultdict(Counter)
    for e in toc:
        if not e.page:
            continue
        key = search_key(e.title)
        for pno in (e.page, e.page + 1):
            cands = [l for l in lines_by_page.get(pno, []) if l.kind == "heading"]
            hit = next((l for l in cands if search_key(l.text) == key), None)
            hit = hit or next((l for l in cands if key and (key in search_key(l.text) or search_key(l.text) in key)), None)
            if hit:
                votes[hit.size][e.level] += 1
                break
    mapped = {size: c.most_common(1)[0][0] for size, c in votes.items()}
    heading_sizes = sorted({l.size for ls in lines_by_page.values() for l in ls if l.kind == "heading"}, reverse=True)
    if not mapped:
        return {s: i + 1 for i, s in enumerate(heading_sizes)}
    out = {}
    for s in heading_sizes:
        if s in mapped:
            out[s] = mapped[s]
        else:
            bigger = [lvl for ms, lvl in mapped.items() if ms > s]
            out[s] = (max(bigger) + 1) if bigger else 1
    return out


def _find_tables(doc, lines_by_page) -> dict[int, list[tuple[tuple, str]]]:
    out: dict[int, list] = {}
    for page in doc:
        try:
            tabs = page.find_tables()
        except Exception:  # noqa: BLE001 - 表検出の失敗は本文抽出を止めない
            continue
        found = []
        for t in tabs.tables:
            rows = t.extract()
            if len(rows) < 2 or t.col_count < 2:
                continue
            md = _rows_markdown(rows)
            if md:
                found.append((tuple(t.bbox), md))
        if found:
            out[page.number + 1] = found
    return out


def _rows_markdown(rows: list[list]) -> str:
    clean = [[_cell_text(c) for c in r] for r in rows]
    if not any(any(c for c in r) for r in clean):
        return ""
    width = max(len(r) for r in clean)
    clean = [r + [""] * (width - len(r)) for r in clean]
    lines = ["| " + " | ".join(clean[0]) + " |", "|" + "---|" * width]
    lines += ["| " + " | ".join(r) + " |" for r in clean[1:]]
    return "\n".join(lines)


_CJK_GAP_RE = re.compile(r"(?<=[\u3040-\u30ff\u4e00-\u9fff]) (?=[\u3040-\u30ff\u4e00-\u9fff、。])")


def _cell_text(c: str | None) -> str:
    # find_tables は和文の字間に空白を挟むことがある（「ツリ ーのハッシュ」）。表セルに限って詰める
    text = normalize_text(join_lines((c or "").split("\n")))
    return _CJK_GAP_RE.sub("", text).replace("|", "\\|")


def _inside(inner, outer, tol=2.0) -> bool:
    cx, cy = (inner[0] + inner[2]) / 2, (inner[1] + inner[3]) / 2
    return outer[0] - tol <= cx <= outer[2] + tol and outer[1] - tol <= cy <= outer[3] + tol


def _page_items(pno: int, lines: list[Line], images: list[tuple], tables: list[tuple]) -> list[Item]:
    """テキスト行はストリーム順を保ち、画像・表は y 座標で差し込む（1 段組前提）。"""
    items: list[Item] = []
    table_boxes = [b for b, _ in tables]
    for ln in lines:
        if ln.kind == "furniture":
            continue
        if any(_inside(ln.bbox, tb) for tb in table_boxes):
            continue
        if any(_inside(ln.bbox, ib) for ib in images):
            continue  # 画像に重なる文字（図中ラベル）は本文に入れない
        items.append(Item(ln.page, ln.bbox, line=ln))
    extras = [Item(pno, b, image=True) for b in images]
    extras += [Item(pno, b, table_md=md) for b, md in tables]
    for ex in sorted(extras, key=lambda e: e.bbox[1]):
        idx = next((i for i, it in enumerate(items) if it.bbox[1] >= ex.bbox[1]), len(items))
        items.insert(idx, ex)
    return items


# --- グループ化 ----------------------------------------------------------------


def _group_page(items: list[Item], body_size, body_left, level_of_size, prev_groups: list[_Group]) -> list[_Group]:
    groups: list[_Group] = []
    cur: _Group | None = None
    prev_line: Line | None = None

    def close():
        nonlocal cur
        if cur is not None:
            groups.append(cur)
        cur = None

    for it in items:
        if it.image:
            close()
            g = _Group("figure", image_bbox=it.bbox, image_page=it.page)
            g.add_bbox(it.page, it.bbox)
            groups.append(g)
            prev_line = None
            continue
        if it.table_md is not None:
            close()
            g = _Group("table", table_md=it.table_md)
            g.add_bbox(it.page, it.bbox)
            groups.append(g)
            prev_line = None
            continue

        ln = it.line
        gap = (ln.y0 - prev_line.y1) if prev_line else 999
        kind = ln.kind

        if kind == "heading":
            if cur and cur.type == "heading" and cur.lines[-1].size == ln.size and gap < ln.size * 0.8:
                cur.lines.append(ln)
            else:
                close()
                cur = _Group("heading", lines=[ln], level=level_of_size.get(ln.size, 1))
        elif kind == "caption":
            close()
            cur = _Group("caption", lines=[ln])
        elif kind == "code":
            if cur and cur.type == "code" and gap < body_size * 2.6:  # コード中の空行を許容
                cur.lines.append(ln)
            else:
                close()
                cur = _Group("code", lines=[ln])
        elif kind == "bullet":
            if cur and cur.type == "list" and gap < body_size * 1.2:
                cur.items.append([ln])
            else:
                close()
                cur = _Group("list", items=[[ln]])
        else:  # body
            if cur and cur.type == "caption" and gap < body_size * 0.6:
                cur.lines.append(ln)
            elif cur and cur.type == "list" and gap < body_size * 0.6 and ln.x0 > cur.items[-1][0].x0 + 2:
                cur.items[-1].append(ln)
            elif cur and cur.type == "paragraph" and not _para_break(cur.lines[-1], ln, gap, body_size, body_left):
                cur.lines.append(ln)
            else:
                close()
                cur = _Group("paragraph", lines=[ln])
        cur.add_bbox(ln.page, ln.bbox)
        prev_line = ln
    close()
    return groups


def _para_break(prev: Line, ln: Line, gap: float, body_size: float, body_left: float) -> bool:
    if gap > body_size * 0.6:
        return True
    if abs(ln.size - prev.size) > 0.6:
        return True
    # 字下げ（1 字程度）で始まる行は新段落（段落間に空きのない和書）
    if ln.x0 > body_left + body_size * 0.6 and prev.x0 <= body_left + 1 and prev.text.rstrip().endswith(SENTENCE_END):
        return True
    return False


# --- ブロック化 ----------------------------------------------------------------


def _locator(g: _Group) -> Locator:
    return Locator(pdf=[PdfLocator(page=p, bbox=tuple(round(v, 1) for v in b)) for p, b in sorted(g.bbox_by_page.items())])


def _code_text(lines: list[Line]) -> str:
    left = min(l.x0 for l in lines)
    out = []
    for l in lines:
        cw = l.size * 0.6  # 等幅フォントの字幅の近似
        indent = max(0, round((l.x0 - left) / cw))
        out.append(" " * indent + l.text.replace("\xa0", " ").rstrip())
    return "\n".join(out)


def _strip_marker(text: str) -> str:
    text = text.strip()
    m = BULLET_RE.match(text)
    if m:
        # ①②… はコード中の注記番号と対応することが多いので本文に残す
        return text if "①" <= text[0] <= "⑳" else text[m.end():]
    m = ORDERED_RE.match(text)
    return text[m.end():] if m else text


def _mark_minor_headings(groups: list[_Group], level: int) -> None:
    """本文と同じサイズの小見出し（「利点」「The Pros」など）を見出しに昇格する。

    条件: 1 行だけの段落・短い・文末記号で終わらない・次が本文系ブロック・（太字 または 句点を含まない）。
    フォントに太字情報がない和書もあるため、太字は必須にしない。
    """
    for i, g in enumerate(groups[:-1]):
        if g.type != "paragraph" or len(g.lines) != 1:
            continue
        ln = g.lines[0]
        text = ln.text.strip()
        if not text or len(text) > 40 or text.endswith(SENTENCE_END + ("、", ",", "，", ";")):
            continue
        nxt = groups[i + 1]
        if nxt.type not in ("paragraph", "list", "code", "table", "figure"):
            continue
        if "。" in text or "、" in text or (not ln.bold and re.search(r"[.!?]\s", text)):
            continue
        if "\u3041" <= text[0] <= "\u309f":
            continue  # ひらがなで始まる行は文の途中（「と打ち込んで…」）であって見出しではない
        g.type = "heading"
        g.level = level
        g.caption = "minor"


def _merge_across_pages(groups: list[_Group], body_left: float) -> list[_Group]:
    """ページをまたぐ段落・コードを連結する（前ページ末が文末記号で終わらず、次ページ先頭が字下げなし）。"""
    merged: list[_Group] = []
    for g in groups:
        prev = merged[-1] if merged else None
        if prev and prev.type == g.type and g.type in ("paragraph", "code") and _crosses_page(prev, g):
            if g.type == "code" or not prev.lines[-1].text.rstrip().endswith(SENTENCE_END) and g.lines[0].x0 <= body_left + 1:
                prev.lines.extend(g.lines)
                for p, b in g.bbox_by_page.items():
                    prev.add_bbox(p, b)
                continue
        merged.append(g)
    return merged


def _vocabulary(lines: list[Line]) -> set[str]:
    """書籍内に出現する英単語（小文字）。行末ハイフンを除去してよいかの判定に使う。"""
    vocab: set[str] = set()
    for ln in lines:
        words = re.findall(r"[A-Za-z]+", ln.text)
        if ln.text.rstrip().endswith("-") and words:
            words = words[:-1]  # 行末の分割片は語彙に入れない
        vocab.update(w.lower() for w in words[1:] if len(w) > 1)  # 行頭の分割片も除く
    return vocab


def _to_blocks(groups: list[_Group], doc, page_labels, body_left, assets_dir: Path | None, vocab: set[str]):
    def dehyphenate(head: str, tail: str) -> bool:
        joined = (head + tail).lower()
        return joined in vocab and f"{head}-{tail}".lower() not in vocab

    def join(lines: list[str]) -> str:
        return normalize_text(join_lines(lines, dehyphenate))

    merged = groups

    # キャプションを直前（なければ直後）の図・表・コードに付ける
    final: list[_Group] = []
    for i, g in enumerate(merged):
        if g.type == "caption":
            text = join([l.text for l in g.lines])
            target = final[-1] if final and final[-1].type in ("figure", "table", "code") and final[-1].caption is None else None
            if target is None and i + 1 < len(merged) and merged[i + 1].type in ("figure", "table", "code"):
                merged[i + 1].caption = text
                continue
            if target is not None:
                target.caption = text
                continue
            fg = _Group("figure", caption=text, bbox_by_page=dict(g.bbox_by_page))
            final.append(fg)
            continue
        final.append(g)

    blocks: list[Block] = []
    assets: list[Asset] = []
    for g in final:
        loc = _locator(g)
        first_page = min(g.bbox_by_page) if g.bbox_by_page else None
        label = page_labels.get(first_page) if first_page else None
        if g.type == "heading":
            text = join([l.text for l in g.lines])
            blk = Block(type="heading", text=text, level=g.level, flags=["minor_heading"] if g.caption == "minor" else [])
        elif g.type == "paragraph":
            blk = Block(type="paragraph", text=join([l.text for l in g.lines]))
        elif g.type == "code":
            blk = Block(type="code", text=_code_text(g.lines), caption=g.caption)
        elif g.type == "list":
            items = [join([_strip_marker(it[0].text)] + [l.text for l in it[1:]]) for it in g.items]
            blk = Block(type="list", text="\n".join(items), items=items)
        elif g.type == "table":
            blk = Block(type="table", text=g.table_md or "", caption=g.caption)
        else:  # figure
            asset_id = None
            if g.image_bbox is not None:
                asset_id = f"img-{len(assets) + 1:04d}"
                rel = f"assets/{asset_id}.png"
                if assets_dir is not None:
                    assets_dir.mkdir(parents=True, exist_ok=True)
                    pix = doc[g.image_page - 1].get_pixmap(clip=pymupdf.Rect(g.image_bbox), dpi=110)
                    pix.save(assets_dir / f"{asset_id}.png")
                assets.append(Asset(asset_id=asset_id, path=rel, media_type="image/png", caption=g.caption))
            blk = Block(type="figure", text=g.caption or "", caption=g.caption, asset_id=asset_id,
                        flags=[] if asset_id else ["no_image_bbox"])
        if not blk.text and blk.type != "figure":
            continue
        blk.locator = loc
        blk.page_label = label
        blocks.append(blk)
    return blocks, assets


def _crosses_page(prev: _Group, g: _Group) -> bool:
    if not prev.bbox_by_page or not g.bbox_by_page:
        return False
    return min(g.bbox_by_page) == max(prev.bbox_by_page) + 1
