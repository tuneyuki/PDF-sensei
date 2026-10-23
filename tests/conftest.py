"""テスト用の小さな EPUB / PDF をその場で生成する（著作物をリポジトリに入れないため）。"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pymupdf
import pytest

CONTAINER = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/package.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>"""

OPF = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="uid">urn:isbn:9784000000002</dc:identifier>
    <dc:title>テスト入門</dc:title>
    <dc:creator id="c1">山田 太郎</dc:creator>
    <dc:creator id="c2">Asciidoctor</dc:creator>
    <meta property="role" refines="#c2">mfr</meta>
    <dc:language>ja</dc:language>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="pre" href="_まえがき.xhtml" media-type="application/xhtml+xml"/>
    <item id="ch1" href="text/ch01.xhtml" media-type="application/xhtml+xml"/>
    <item id="img1" href="images/fig1.png" media-type="image/png"/>
  </manifest>
  <spine><itemref idref="pre"/><itemref idref="ch1"/></spine>
</package>"""

NAV = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body>
<nav epub:type="toc"><ol>
  <li><a href="_まえがき.xhtml">まえがき</a></li>
  <li><a href="text/ch01.xhtml">第1章 需要予測</a><ol>
    <li><a href="text/ch01.xhtml#s1">1.1 基本</a></li>
  </ol></li>
</ol></nav></body></html>"""

PREFACE = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>まえがき</title></head><body>
<p>本書は需要予測の
入門書です。</p></body></html>"""

CH1 = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body>
<section epub:type="chapter" id="ch1">
<h1>第1章 需要予測</h1>
<p>需要予測とは、将来の<ruby>需要<rt>じゅよう</rt></ruby>を
見積もることです。</p>
<section id="s1"><h2>1.1 基本</h2>
<p>移動平均は
simple な手法です。</p>
<aside class="admonition note" epub:type="note"><h2>注意</h2><div class="content"><p>外れ値に弱い。</p></div></aside>
<figure class="image"><img src="../images/fig1.png" alt="グラフ"/><figcaption>図1 売上の推移</figcaption></figure>
<pre class="source language-python"><code>def ma(xs, n):
    return sum(xs[-n:]) / n</code></pre>
<ul><li>長所: 簡単<ul><li>計算が軽い</li></ul></li><li>短所: 遅れる</li></ul>
</section>
</section></body></html>"""

PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
)


@pytest.fixture
def sample_epub(tmp_path: Path) -> Path:
    p = tmp_path / "sample.epub"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", CONTAINER)
        z.writestr("OEBPS/package.opf", OPF)
        z.writestr("OEBPS/nav.xhtml", NAV)
        z.writestr("OEBPS/_まえがき.xhtml", PREFACE)
        z.writestr("OEBPS/text/ch01.xhtml", CH1)
        z.writestr("OEBPS/images/fig1.png", PNG_1PX)
    return p


@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    """2 ページの和文 PDF。見出し・折り返し段落（ページまたぎ）・コード・ノンブル・しおり付き。"""
    p = tmp_path / "sample.pdf"
    doc = pymupdf.open()
    body = ["需要予測とは、将来の需要を見積もることです。移動平均は", "もっとも単純な手法で、直近の値を平均します。"]
    for i in range(2):
        page = doc.new_page(width=595, height=842)
        y = 72
        if i == 0:
            page.insert_text((60, y), "第1章 需要予測", fontsize=20, fontname="japan")
            y += 40
            page.insert_text((60, y), "1.1 基本", fontsize=14, fontname="japan")
            y += 28
            for _ in range(3):
                for line in body:
                    page.insert_text((60, y), line, fontsize=10, fontname="japan")
                    y += 15
                y += 12
            page.insert_text((70, y), "def ma(xs, n):", fontsize=9, fontname="cour")
            y += 13
            page.insert_text((70, y), "    return sum(xs[-n:]) / n", fontsize=9, fontname="cour")
            y += 25
            # ページ末で文が終わらない段落（次ページへ続く）
            page.insert_text((60, 780), "この段落は次のページへ", fontsize=10, fontname="japan")
        else:
            page.insert_text((60, 72), "続いています。", fontsize=10, fontname="japan")
            page.insert_text((60, 110), "1.2 応用", fontsize=14, fontname="japan")
            page.insert_text((60, 140), "指数平滑法は重みを付けます。", fontsize=10, fontname="japan")
        page.insert_text((290, 815), str(i + 11), fontsize=8, fontname="helv")
    doc.set_toc([[1, "第1章 需要予測", 1], [2, "1.1 基本", 1], [2, "1.2 応用", 2]])
    doc.set_metadata({"title": "テスト入門", "author": "山田 太郎"})
    doc.save(p)
    return p
