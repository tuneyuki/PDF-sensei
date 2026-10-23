"""DocTree → Markdown（人のレビュー用・LLM への章単位投入用）。"""

from __future__ import annotations

from .model import Block, DocTree


def render_block(b: Block, assets: dict[str, str] | None = None, anchors: bool = False) -> str:
    if b.type == "heading":
        out = "#" * min(6, b.level or 1) + " " + b.text
    elif b.type == "code":
        fence = "```" + (b.language or "")
        out = (f"*{b.caption}*\n\n" if b.caption else "") + f"{fence}\n{b.text}\n```"
    elif b.type == "list":
        out = "\n".join(_list_line(i) for i in (b.items or b.text.split("\n")))
    elif b.type == "table":
        out = (f"*{b.caption}*\n\n" if b.caption else "") + b.text
    elif b.type == "figure":
        path = (assets or {}).get(b.asset_id or "", "")
        label = b.caption or b.text or "figure"
        out = f"![{label}]({path})" if path else f"*[図] {label}*"
    elif b.type == "quote":
        out = "\n".join("> " + line if line else ">" for line in b.text.split("\n"))
    elif b.type in ("note", "footnote"):
        head = f"**{b.caption}**: " if b.caption else ""
        out = "\n".join("> " + line if line else ">" for line in (head + b.text).split("\n"))
    else:
        out = b.text
    if anchors:
        page = ""
        if b.locator.pdf:
            page = f" p.{b.page_label or b.locator.pdf[0].page}"
        out = f"<!-- {b.block_id}{page} -->\n{out}"
    return out


def _list_line(item: str) -> str:
    stripped = item.lstrip(" ")
    indent = len(item) - len(stripped)
    return " " * indent + "- " + stripped


def render_markdown(doc: DocTree, anchors: bool = False, section_ids: set[str] | None = None) -> str:
    assets = {a.asset_id: a.path for a in doc.assets}
    parts = []
    if section_ids is None:
        title = doc.book.title or doc.book.source_filename
        meta = [f"# {title}", ""]
        if doc.book.authors:
            meta.append("著者: " + ", ".join(doc.book.authors))
        meta.append(f"source: {doc.book.source_filename} ({doc.book.source_format}, sha256 {doc.book.source_sha256[:12]})")
        parts.append("\n".join(meta))
    for b in doc.blocks:
        if section_ids is not None and b.section_id not in section_ids:
            continue
        if b.type == "heading":
            # 書籍タイトルを # にするので、本文の見出しは 1 段下げる
            b = b.model_copy(update={"level": (b.level or 1) + (1 if section_ids is None else 0)})
        parts.append(render_block(b, assets, anchors))
    return "\n\n".join(parts) + "\n"
