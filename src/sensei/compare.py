"""同一書籍の 2 つの DocTree を突き合わせて抽出品質を測る。

典型的には A=EPUB（構造が明示されている参照）, B=PDF（抽出器の評価対象）。
- 見出し再現率: A の本文見出しが B でも見出しとして取れているか（レベル別）
- 本文再現率: A の段落・コードが B の本文に含まれているか（空白無視／空白込み）
- 段落境界一致率: A の段落が B の 1 ブロックとして丸ごと一致するか
- 章ごとの文字数比
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path

from .model import DocTree
from .textnorm import search_key


def _loose(s: str) -> str:
    return search_key(s)


def _strict(s: str) -> str:
    import unicodedata

    s = unicodedata.normalize("NFKC", s)
    s = s.replace(" ", " ").replace(" ", " ")
    return re.sub(r"\s+", " ", s).strip()


def _subtree_chars(doc: DocTree) -> dict[str, int]:
    return {s.section_id: s.char_count for s in doc.sections}


def compare(a: DocTree, b: DocTree) -> dict:
    sec_a = {s.section_id: s for s in a.sections}
    sec_b = {s.section_id: s for s in b.sections}

    # 見出し
    head_b = Counter(_loose(s.title) for s in b.sections)
    by_level = defaultdict(lambda: [0, 0])
    level_ok = defaultdict(lambda: [0, 0])
    b_level = defaultdict(list)
    for s in b.sections:
        b_level[_loose(s.title)].append(s.level)
    for s in a.sections:
        if s.role != "body":
            continue
        k = _loose(s.title)
        by_level[s.level][1] += 1
        if head_b[k]:
            by_level[s.level][0] += 1
            level_ok[s.level][1] += 1
            if s.level in b_level[k]:
                level_ok[s.level][0] += 1

    # B の見出しのうち A にもある割合（誤検出の少なさ）
    head_a = {_loose(s.title) for s in a.sections}
    b_body = [s for s in b.sections if s.role == "body"]
    heading_precision = sum(1 for s in b_body if _loose(s.title) in head_a) / max(1, len(b_body))

    # 本文
    body_a = [blk for blk in a.blocks if sec_a[blk.section_id].role == "body"]
    b_loose = _loose("".join(x.text for x in b.blocks))
    b_strict = "\n".join(_strict(x.text) for x in b.blocks)
    b_para_set = {_strict(x.text) for x in b.blocks}
    stats = defaultdict(lambda: Counter())
    misses = defaultdict(list)
    for blk in body_a:
        if blk.type not in ("paragraph", "code", "list") or len(blk.text) < 20:
            continue
        st = stats[blk.type]
        st["n"] += 1
        if blk.type == "code":
            lines = [ln for ln in blk.text.split("\n") if len(ln.strip()) >= 4]
            st["lines"] += len(lines)
            st["lines_found"] += sum(1 for ln in lines if _loose(ln) in b_loose)
            st["found_loose"] += all(_loose(ln) in b_loose for ln in lines)
            continue
        lo, sk = _loose(blk.text), _strict(blk.text)
        if lo in b_loose:
            st["found_loose"] += 1
        elif len(misses[blk.type]) < 15:
            misses[blk.type].append({"block_id": blk.block_id, "text": blk.text[:160]})
        if sk in b_strict:
            st["found_strict"] += 1
        if sk in b_para_set:
            st["same_boundary"] += 1

    # 章ごとの文字数
    a_l1 = [s for s in a.sections if s.level == 1 and s.role == "body"]
    b_l1 = [s for s in b.sections if s.level == 1]
    chapters = []
    for s in a_l1:
        k = _loose(s.title)
        # 「付録 A: X」と「X」のような表記ゆれは部分一致で拾う
        t = next((x for x in b_l1 if _loose(x.title) == k), None) or next(
            (x for x in b_l1 if k and k in _loose(x.title)), None
        )
        chapters.append({
            "title": s.title,
            "a_chars": s.char_count,
            "b_chars": t.char_count if t else None,
            "ratio": round(t.char_count / s.char_count, 3) if t and s.char_count else None,
            "b_pages": f"{t.page_start}-{t.page_end}" if t and t.page_start else None,
        })

    summary = {
        "a": f"{a.book.source_filename} [{a.book.extraction.extractor}]",
        "b": f"{b.book.source_filename} [{b.book.extraction.extractor}]",
        "headings_recall_by_level": {lvl: round(v[0] / v[1], 3) for lvl, v in sorted(by_level.items()) if v[1]},
        "heading_level_agreement": {lvl: round(v[0] / v[1], 3) for lvl, v in sorted(level_ok.items()) if v[1]},
        "heading_precision": round(heading_precision, 3),
        "text": {},
        "blocks_by_type": {"a": a.stats["blocks_by_type"], "b": b.stats["blocks_by_type"]},
        "chars_body_role": {"a": a.stats["chars_body_role"], "b": b.stats["chars_body_role"]},
    }
    for t, st in stats.items():
        n = st["n"] or 1
        d = {"n": st["n"], "recall_loose": round(st["found_loose"] / n, 3)}
        if t == "code":
            d["line_recall"] = round(st["lines_found"] / (st["lines"] or 1), 3)
        else:
            d["recall_strict"] = round(st["found_strict"] / n, 3)
            d["same_boundary"] = round(st["same_boundary"] / n, 3)
        summary["text"][t] = d

    md = _markdown(summary, chapters, misses)
    return {"summary": summary, "chapters": chapters, "misses": dict(misses), "markdown": md}


def _markdown(summary, chapters, misses) -> str:
    lines = [f"## {summary['a']}  vs  {summary['b']}", ""]
    lines += ["| 指標 | 値 |", "|---|---|"]
    for lvl, v in summary["headings_recall_by_level"].items():
        agree = summary["heading_level_agreement"].get(lvl)
        lines.append(f"| 見出し再現率 L{lvl}（レベル一致） | {v:.1%}（{agree:.1%}） |" if agree is not None else f"| 見出し再現率 L{lvl} | {v:.1%} |")
    lines.append(f"| 見出し適合率（B の見出しが A にもある割合） | {summary['heading_precision']:.1%} |")
    for t, d in summary["text"].items():
        if t == "code":
            lines.append(f"| code n={d['n']} 行再現率 / ブロック完全一致 | {d['line_recall']:.1%} / {d['recall_loose']:.1%} |")
        else:
            lines.append(f"| {t} n={d['n']} 再現率（空白無視 / 空白込み / 境界一致） | {d['recall_loose']:.1%} / {d['recall_strict']:.1%} / {d['same_boundary']:.1%} |")
    lines.append(f"| 本文文字数 A / B | {summary['chars_body_role']['a']:,} / {summary['chars_body_role']['b']:,} |")
    lines += ["", "| 章 | A 文字数 | B 文字数 | B/A | B ページ |", "|---|---:|---:|---:|---|"]
    for c in chapters:
        lines.append(f"| {c['title']} | {c['a_chars']:,} | {c['b_chars'] if c['b_chars'] is not None else '-':,} | {c['ratio'] if c['ratio'] is not None else '-'} | {c['b_pages'] or '-'} |"
                     if c["b_chars"] is not None else f"| {c['title']} | {c['a_chars']:,} | - | - | - |")
    for t, ms in misses.items():
        if ms:
            lines += ["", f"<details><summary>B に見つからない {t}（先頭 {len(ms)} 件）</summary>", ""]
            lines += [f"- `{m['block_id']}` {m['text']}" for m in ms]
            lines += ["", "</details>"]
    return "\n".join(lines) + "\n"


def compare_files(a: Path, b: Path) -> dict:
    return compare(DocTree.model_validate_json(a.read_text()), DocTree.model_validate_json(b.read_text()))
