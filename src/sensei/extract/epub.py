"""[1] Extract: EPUB → RawDoc。

container.xml → OPF（メタデータ・manifest・spine）→ nav/ncx（目次）→ spine 順に XHTML を走査して
ブロック化する。見出しは本文構造のもの（h1-h6）だけを heading とし、囲み記事や図の中の見出しは
note/figure の一部として扱う。
"""

from __future__ import annotations

import posixpath
import re
import unicodedata
import zipfile
from pathlib import Path
from urllib.parse import unquote

from lxml import etree, html as lxml_html

from ..intake import IntakeResult
from ..model import Asset, Block, BookMeta, EpubLocator, Extraction, Locator, RawDoc, TocEntry
from ..textnorm import collapse_whitespace, normalize_text

EXTRACTOR_VERSION = "0.1.0"

NS = {
    "c": "urn:oasis:names:tc:opendocument:xmlns:container",
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
    "ncx": "http://www.daisy.org/z3986/2005/ncx/",
}
EPUB_NS = "http://www.idpf.org/2007/ops"

SKIP_TAGS = {"script", "style", "head", "nav", "svg", "math", "template", "noscript"}
HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
# 本文構造の見出しとして扱わない（中の見出しは付属物のタイトル）
CONTAINER_TAGS = {"aside", "figure", "table", "blockquote"}
BLOCK_TAGS = HEADING_TAGS | {
    "p", "pre", "ul", "ol", "dl", "table", "figure", "blockquote", "aside", "div", "section",
    "header", "footer", "article", "main", "hr", "img", "figcaption",
}
FOOTNOTE_TYPES = {"footnote", "endnote", "rearnote", "note"}


def _local(tag) -> str:
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1].lower()


def _epub_type(el) -> str:
    return el.get(f"{{{EPUB_NS}}}type") or el.get("epub:type") or ""


class _DocContext:
    def __init__(self, href: str, spine_step: str, assets_dir: Path | None, z: "_Zip", state: dict):
        self.href = href
        self.spine_step = spine_step
        self.assets_dir = assets_dir
        self.z = z
        self.state = state  # 書籍全体で共有（asset 採番など）
        self.blocks: list[Block] = []


class _Zip:
    """ZIP 内のファイル名ゆれ（UTF-8 フラグなしの日本語名、NFC/NFD の違い）を吸収して読む。"""

    def __init__(self, path: Path):
        self._z = zipfile.ZipFile(path)
        self._names: dict[str, str] = {}
        for info in self._z.infolist():
            name = info.filename
            if not info.flag_bits & 0x800:
                try:
                    name = name.encode("cp437").decode("utf-8")
                except (UnicodeEncodeError, UnicodeDecodeError):
                    pass
            self._names[unicodedata.normalize("NFC", name)] = info.filename

    def read(self, name: str) -> bytes:
        real = self._names.get(unicodedata.normalize("NFC", name))
        if real is None:
            raise KeyError(name)
        return self._z.read(real)

    def close(self) -> None:
        self._z.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def extract_epub(src: IntakeResult, assets_dir: Path | None = None) -> RawDoc:
    warnings: list[str] = []
    with _Zip(src.path) as z:
        container = etree.fromstring(z.read("META-INF/container.xml"))
        opf_path = container.find(".//c:rootfile", NS).get("full-path")
        opf = etree.fromstring(z.read(opf_path))
        opf_dir = posixpath.dirname(opf_path)

        meta = _read_metadata(opf, src)

        manifest = {}
        for item in opf.find("opf:manifest", NS):
            if _local(item.tag) != "item":
                continue
            href = _resolve(opf_dir, item.get("href"))
            manifest[item.get("id")] = {
                "href": href,
                "media_type": item.get("media-type", ""),
                "properties": (item.get("properties") or "").split(),
            }

        spine_el = opf.find("opf:spine", NS)
        package_children = [c for c in opf if isinstance(c.tag, str)]
        spine_step = (package_children.index(spine_el) + 1) * 2
        spine = []
        for i, ref in enumerate(c for c in spine_el if _local(c.tag) == "itemref"):
            idref = ref.get("idref")
            if idref in manifest and ref.get("linear", "yes") != "no":
                spine.append((idref, manifest[idref]["href"], f"/{spine_step}/{(i + 1) * 2}[{idref}]"))

        toc = _read_nav(z, manifest, opf_dir) or _read_ncx(z, manifest, spine_el)
        if not toc:
            warnings.append("目次（nav/ncx）が見つかりません")
        toc_title_by_href = {}
        for e in toc:
            if e.href and "#" not in e.href:
                toc_title_by_href.setdefault(e.href, e.title)

        state = {"asset_seq": 0, "assets": [], "asset_by_href": {}}
        blocks: list[Block] = []
        for idref, href, step in spine:
            if manifest[idref]["media_type"] not in ("application/xhtml+xml", "text/html"):
                continue
            if "nav" in manifest[idref]["properties"]:
                continue
            ctx = _DocContext(href, step, assets_dir, z, state)
            root = _parse_xhtml(z.read(href))
            body = next((c for c in root if _local(c.tag) == "body"), None)
            if body is None:
                warnings.append(f"body がありません: {href}")
                continue
            _walk(body, ctx, in_container=False, section_type="")
            doc_blocks = ctx.blocks
            if doc_blocks and doc_blocks[0].type != "heading":
                # 見出しのない spine 文書（扉・ライセンス等）は目次タイトルで節を立てる
                title = toc_title_by_href.get(href) or _doc_title(root) or posixpath.basename(href)
                doc_blocks.insert(
                    0,
                    Block(
                        type="heading", text=title, level=1,
                        locator=Locator(epub=EpubLocator(href=href, cfi=f"epubcfi({step}!/4)")),
                        flags=["synthetic"],
                    ),
                )
            blocks.extend(doc_blocks)

    return RawDoc(
        meta=meta,
        toc=toc,
        blocks=blocks,
        assets=state["assets"],
        warnings=warnings,
    )


def _resolve(base_dir: str, href: str) -> str:
    href = unquote(href.split("#", 1)[0])
    return posixpath.normpath(posixpath.join(base_dir, href)) if base_dir else posixpath.normpath(href)


def _parse_xhtml(data: bytes):
    try:
        return etree.fromstring(data, etree.XMLParser(recover=True, resolve_entities=False, no_network=True))
    except etree.XMLSyntaxError:
        return lxml_html.fromstring(data)


def _doc_title(root) -> str | None:
    for el in root.iter():
        if _local(el.tag) == "title" and (el.text or "").strip():
            return el.text.strip()
    return None


def _read_metadata(opf, src: IntakeResult) -> BookMeta:
    md = opf.find("opf:metadata", NS)

    def texts(tag):
        return [normalize_text(e.text or "") for e in md.findall(f"dc:{tag}", NS) if (e.text or "").strip()]

    # creator の role（refines の meta または opf:role 属性）が aut 以外のものは除外
    roles = {}
    for m in md.findall("opf:meta", NS):
        if m.get("property") == "role" and m.get("refines"):
            roles[m.get("refines").lstrip("#")] = (m.text or "").strip()
    authors = []
    for e in md.findall("dc:creator", NS):
        role = roles.get(e.get("id", ""), e.get(f"{{{NS['opf']}}}role", "aut"))
        if role in ("aut", "") and (e.text or "").strip():
            authors.append(normalize_text(e.text))

    isbn = None
    for ident in texts("identifier"):
        digits = re.sub(r"[^0-9Xx]", "", ident.removeprefix("urn:isbn:"))
        if len(digits) in (10, 13) and (ident.lower().startswith("urn:isbn") or digits.startswith("97")):
            isbn = digits
            break

    title = (texts("title") or [None])[0]
    return BookMeta(
        book_id=src.default_book_id,
        source_sha256=src.sha256,
        source_format="epub",
        source_filename=src.path.name,
        title=title,
        authors=authors,
        publisher=(texts("publisher") or [None])[0],
        isbn=isbn,
        language=(texts("language") or [None])[0],
        published_at=(texts("date") or [None])[0],
        extraction=Extraction(extractor="epub-xhtml", extractor_version=EXTRACTOR_VERSION),
    )


def _read_nav(z, manifest, opf_dir) -> list[TocEntry]:
    nav_item = next((m for m in manifest.values() if "nav" in m["properties"]), None)
    if not nav_item:
        return []
    root = _parse_xhtml(z.read(nav_item["href"]))
    nav_dir = posixpath.dirname(nav_item["href"])
    toc_nav = next((e for e in root.iter() if _local(e.tag) == "nav" and _epub_type(e) == "toc"), None)
    if toc_nav is None:
        return []
    entries: list[TocEntry] = []

    def walk_ol(ol, level):
        for li in ol:
            if _local(li.tag) != "li":
                continue
            a = next((c for c in li if _local(c.tag) in ("a", "span")), None)
            if a is not None:
                href = a.get("href")
                frag = href.split("#", 1)[1] if href and "#" in href else None
                full = _resolve(nav_dir, href) + (f"#{unquote(frag)}" if frag else "") if href else None
                entries.append(TocEntry(level=level, title=collapse_whitespace("".join(a.itertext())), href=full))
            for sub in li:
                if _local(sub.tag) == "ol":
                    walk_ol(sub, level + 1)

    ol = next((c for c in toc_nav.iter() if _local(c.tag) == "ol"), None)
    if ol is not None:
        walk_ol(ol, 1)
    return entries


def _read_ncx(z, manifest, spine_el) -> list[TocEntry]:
    ncx_id = spine_el.get("toc")
    ncx_item = manifest.get(ncx_id) if ncx_id else None
    if not ncx_item:
        ncx_item = next((m for m in manifest.values() if m["media_type"] == "application/x-dtbncx+xml"), None)
    if not ncx_item:
        return []
    root = etree.fromstring(z.read(ncx_item["href"]))
    ncx_dir = posixpath.dirname(ncx_item["href"])
    entries: list[TocEntry] = []

    def walk(parent, level):
        for np_ in parent.findall("ncx:navPoint", NS):
            label = np_.find("ncx:navLabel/ncx:text", NS)
            content = np_.find("ncx:content", NS)
            src = content.get("src") if content is not None else None
            frag = src.split("#", 1)[1] if src and "#" in src else None
            href = _resolve(ncx_dir, src) + (f"#{unquote(frag)}" if frag else "") if src else None
            entries.append(TocEntry(level=level, title=collapse_whitespace(label.text or "") if label is not None else "", href=href))
            walk(np_, level + 1)

    nav_map = root.find("ncx:navMap", NS)
    if nav_map is not None:
        walk(nav_map, 1)
    return entries


# --- 本文の走査 -------------------------------------------------------------


def _cfi(el, ctx: _DocContext) -> str:
    steps = []
    cur = el
    while cur is not None and cur.getparent() is not None:
        parent = cur.getparent()
        siblings = [c for c in parent if isinstance(c.tag, str)]
        idx = siblings.index(cur)
        step = f"/{(idx + 1) * 2}"
        if cur.get("id"):
            step += f"[{cur.get('id')}]"
        steps.append(step)
        cur = parent
    return f"epubcfi({ctx.spine_step}!{''.join(reversed(steps))})"


def _locator(el, ctx: _DocContext) -> Locator:
    cur, element_id = el, None
    while cur is not None:
        if cur.get("id"):
            element_id = cur.get("id")
            break
        cur = cur.getparent()
    return Locator(epub=EpubLocator(href=ctx.href, element_id=element_id, cfi=_cfi(el, ctx)))


def _inline_text(el, ruby: list | None = None, include_tail: bool = False) -> str:
    """インライン要素のテキスト。ルビ（rt/rp）は本文から除き ruby に (親文字, 読み) を集める。"""
    parts: list[str] = []

    def rec(e):
        tag = _local(e.tag)
        if tag in SKIP_TAGS:
            return
        if tag in ("rt", "rp"):
            return
        if tag == "br":
            parts.append("\n")
            return
        if tag == "img":
            alt = (e.get("alt") or "").strip()
            if alt:
                parts.append(alt)
            return
        if tag == "ruby":
            # <ruby>漢<rt>かん</rt>字<rt>じ</rt></ruby> の親文字は rt の tail にも分かれて入る
            base = e.text or ""
            for c in e:
                if _local(c.tag) not in ("rt", "rp"):
                    base += _inline_text(c)
                base += c.tail or ""
            reading = "".join("".join(r.itertext()) for r in e if _local(r.tag) == "rt")
            parts.append(base)
            if ruby is not None and reading:
                ruby.append([base.strip(), reading.strip()])
            return
        if e.text:
            parts.append(e.text)
        for c in e:
            rec(c)
            if c.tail:
                parts.append(c.tail)

    rec(el)
    if include_tail and el.tail:
        parts.append(el.tail)
    return "".join(parts)


def _clean(s: str) -> str:
    return normalize_text(collapse_whitespace(s))


def _has_block_child(el) -> bool:
    return any(_local(c.tag) in BLOCK_TAGS | {"ul", "ol", "dl"} for c in el)


def _add(ctx: _DocContext, block: Block, el) -> None:
    if not block.text and block.type not in ("figure",):
        return
    if block.locator.epub is None:
        block.locator = _locator(el, ctx)
    ctx.blocks.append(block)


def _code_language(el) -> str | None:
    for e in [el, *el.iter()]:
        m = re.search(r"language-([\w+#-]+)", e.get("class") or "")
        if m:
            return m.group(1)
        if e.get("data-lang"):
            return e.get("data-lang")
    return None


def _caption(el) -> str | None:
    for c in el.iter():
        if _local(c.tag) in ("figcaption", "caption"):
            return _clean(_inline_text(c)) or None
    return None


def _list_items(el, depth=0) -> list[str]:
    items = []
    for li in el:
        tag = _local(li.tag)
        if tag == "li":
            own = "".join(
                [li.text or ""]
                + [_inline_text(c, include_tail=True) if _local(c.tag) not in ("ul", "ol") else (c.tail or "") for c in li]
            )
            text = _clean(own)
            if text:
                items.append("  " * depth + text)
            for c in li:
                if _local(c.tag) in ("ul", "ol"):
                    items.extend(_list_items(c, depth + 1))
        elif tag in ("dt", "dd"):
            text = _clean(_inline_text(li))
            if text:
                items.append(("  " if tag == "dd" else "") + text)
    return items


def _table_markdown(el) -> str:
    rows = []
    for tr in el.iter():
        if _local(tr.tag) != "tr":
            continue
        cells = [_clean(_inline_text(td)).replace("|", "\\|") for td in tr if _local(td.tag) in ("td", "th")]
        if cells:
            rows.append(cells)
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * width]
    lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(lines)


def _image_asset(img, ctx: _DocContext) -> Asset | None:
    src = img.get("src") or img.get("{http://www.w3.org/1999/xlink}href")
    if not src or src.startswith("data:"):
        return None
    href = _resolve(posixpath.dirname(ctx.href), src)
    st = ctx.state
    if href in st["asset_by_href"]:
        return st["asset_by_href"][href]
    try:
        data = ctx.z.read(href)
    except KeyError:
        return None
    st["asset_seq"] += 1
    ext = posixpath.splitext(href)[1].lower() or ".bin"
    asset_id = f"img-{st['asset_seq']:04d}"
    rel = f"assets/{asset_id}{ext}"
    if ctx.assets_dir is not None:
        ctx.assets_dir.mkdir(parents=True, exist_ok=True)
        (ctx.assets_dir / f"{asset_id}{ext}").write_bytes(data)
    media = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".svg": "image/svg+xml", ".webp": "image/webp"}.get(ext, "application/octet-stream")
    asset = Asset(asset_id=asset_id, path=rel, media_type=media, alt=(img.get("alt") or None))
    st["asset_by_href"][href] = asset
    st["assets"].append(asset)
    return asset


def _container_text(el) -> str:
    """aside/blockquote など、中に複数段落を持つ要素のテキスト（見出しは除く）。"""
    paras = []
    taken = set()
    for c in el.iter():
        tag = _local(c.tag)
        if any(a in taken for a in c.iterancestors()):
            continue
        if tag in ("p", "pre", "li", "dd", "dt") or (tag in ("div",) and not _has_block_child(c) and (c.text or "").strip()):
            taken.add(c)
            t = "".join(c.itertext()) if tag == "pre" else _clean(_inline_text(c))
            if t and t.strip():
                paras.append(t.strip())
    if not paras:
        paras = [_clean(_inline_text(el))]
    return "\n\n".join(p for p in paras if p)


def _walk(el, ctx: _DocContext, in_container: bool, section_type: str) -> None:
    for child in el:
        tag = _local(child.tag)
        if not tag or tag in SKIP_TAGS:
            continue
        etype = _epub_type(child)

        if tag in HEADING_TAGS:
            text = _clean(_inline_text(child))
            if not text:
                continue
            if in_container:
                _add(ctx, Block(type="paragraph", text=text, flags=["container_heading"]), child)
            else:
                annotations = {"epub_type": section_type} if section_type else None
                ruby: list = []
                text = _clean(_inline_text(child, ruby))
                if ruby:
                    annotations = {**(annotations or {}), "ruby": ruby}
                _add(ctx, Block(type="heading", text=text, level=int(tag[1]), annotations=annotations), child)
        elif tag == "p" or (tag == "div" and not _has_block_child(child) and (child.text or "").strip()):
            ruby = []
            text = _clean(_inline_text(child, ruby))
            _add(ctx, Block(type="paragraph", text=text, annotations={"ruby": ruby} if ruby else None), child)
        elif tag == "pre":
            _add(ctx, Block(type="code", text="".join(child.itertext()).strip("\n"), language=_code_language(child)), child)
        elif tag in ("ul", "ol", "dl"):
            items = _list_items(child)
            if items:
                _add(ctx, Block(type="list", text="\n".join(items), items=items), child)
        elif tag == "table":
            _add(ctx, Block(type="table", text=_table_markdown(child), caption=_caption(child)), child)
        elif tag == "figure":
            _figure(child, ctx)
        elif tag == "img":
            asset = _image_asset(child, ctx)
            if asset:
                _add(ctx, Block(type="figure", text=asset.alt or "", asset_id=asset.asset_id), child)
        elif tag == "blockquote":
            _add(ctx, Block(type="quote", text=_container_text(child)), child)
        elif tag == "aside" or etype in FOOTNOTE_TYPES:
            is_fn = any(t in FOOTNOTE_TYPES - {"note"} for t in etype.split()) or "footnote" in (child.get("class") or "")
            title = next((_clean(_inline_text(h)) for h in child.iter() if _local(h.tag) in HEADING_TAGS), None)
            title = title or (child.get("title") or None)
            body_parts = []
            for c in child:
                if _local(c.tag) in HEADING_TAGS:
                    continue
                body_parts.append(_container_text(c) if isinstance(c.tag, str) else "")
            text = "\n\n".join(p for p in body_parts if p) or _clean(_inline_text(child))
            _add(ctx, Block(type="footnote" if is_fn else "note", text=text, caption=title), child)
        elif tag == "hr":
            continue
        else:
            # section / div / header 等はたどる。epub:type（chapter, preface, appendix…）は見出しに伝える
            _walk(child, ctx, in_container or tag in CONTAINER_TAGS, etype or section_type)


def _figure(el, ctx: _DocContext) -> None:
    caption = _caption(el)
    pre = next((c for c in el.iter() if _local(c.tag) == "pre"), None)
    if pre is not None:
        _add(ctx, Block(type="code", text="".join(pre.itertext()).strip("\n"), language=_code_language(pre), caption=caption), el)
        return
    table = next((c for c in el.iter() if _local(c.tag) == "table"), None)
    if table is not None:
        _add(ctx, Block(type="table", text=_table_markdown(table), caption=caption), el)
        return
    imgs = [c for c in el.iter() if _local(c.tag) in ("img", "image")]
    if imgs:
        asset = _image_asset(imgs[0], ctx)
        if asset is not None:
            asset.caption = caption
        text = caption or (asset.alt if asset else "") or ""
        _add(ctx, Block(type="figure", text=text, caption=caption, asset_id=asset.asset_id if asset else None), el)
        return
    _walk(el, ctx, True, "")
