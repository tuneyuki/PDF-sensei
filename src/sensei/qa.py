"""[7] QA: Canonical の自動品質チェック。閾値を割ったら review（人手確認）に回す。"""

from __future__ import annotations

import re
from collections import Counter

from .model import DocTree

PUA_RE = re.compile("[-]")


def check(doc: DocTree) -> dict:
    checks: list[dict] = []

    def add(name: str, level: str, ok: bool, value, threshold, message: str):
        checks.append({"check": name, "level": level, "ok": ok, "value": value, "threshold": threshold, "message": message})

    s = doc.stats
    text = "".join(b.text for b in doc.blocks)
    total = max(1, len(text))

    add("has_body_text", "fail", s.get("chars_body_role", 0) > 0, s.get("chars_body_role", 0), "> 0",
        "本文（role=body）のテキストがありません")

    ratio = (s.get("toc_matched", 0) / s["toc_entries"]) if s.get("toc_entries") else None
    add("toc_match_rate", "review", ratio is None or ratio >= 0.8, None if ratio is None else round(ratio, 3), ">= 0.8",
        "目次の項目と本文の見出しが対応していません（見出し検出漏れ・レベル誤り）")

    bad = text.count("�") + len(PUA_RE.findall(text))
    add("garbled_chars", "review", bad / total < 0.0005, round(bad / total, 6), "< 0.0005",
        "置換文字・私用領域の文字が多い（文字化け・フォント埋め込みの問題）")

    if doc.book.source_format == "pdf" and doc.book.page_count:
        chars_by_page: Counter = Counter()
        for b in doc.blocks:
            for loc in b.locator.pdf or []:
                chars_by_page[loc.page] += len(b.text) / len(b.locator.pdf)
        low = [p for p in range(1, doc.book.page_count + 1) if chars_by_page[p] < 20]
        r = len(low) / doc.book.page_count
        add("low_text_pages", "review", r < 0.1, round(r, 3), "< 0.1",
            f"テキストがほぼないページ {len(low)} / {doc.book.page_count}（スキャン・画像ページの可能性。例: {low[:8]}）")

    children = Counter(sec.parent_id for sec in doc.sections if sec.parent_id)
    huge = [sec.title for sec in doc.sections if sec.role == "body" and not children[sec.section_id] and sec.char_count > 40000]
    add("oversized_leaf_sections", "review", not huge, len(huge), "== 0",
        f"小見出しがなく巨大な節があります（見出し検出漏れの可能性）: {huge[:5]}")

    empty = [sec.title for sec in doc.sections if sec.char_count <= len(sec.title) and not children[sec.section_id]]
    add("empty_sections", "review", len(empty) <= max(2, len(doc.sections) * 0.02), len(empty), "<= 2%",
        f"中身のない見出し（誤検出の可能性）: {empty[:8]}")

    no_img = sum(1 for b in doc.blocks if b.type == "figure" and not b.asset_id)
    add("figures_without_image", "info", True, no_img, "-", "画像を特定できなかった図（ベクター図など）")

    status = "ok"
    if any(not c["ok"] and c["level"] == "fail" for c in checks):
        status = "fail"
    elif any(not c["ok"] and c["level"] == "review" for c in checks):
        status = "review"
    return {"status": status, "checks": checks}
