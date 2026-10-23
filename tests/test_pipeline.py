import json
import zipfile

import pytest

from sensei.intake import IntakeError, intake
from sensei.pipeline import IngestOptions, ingest


def test_epub_ingest(sample_epub, tmp_path):
    res = ingest(sample_epub, IngestOptions(out_dir=tmp_path / "out"))
    doc = res.doc

    assert doc.book.title == "テスト入門"
    assert doc.book.authors == ["山田 太郎"]  # role=mfr の creator は除外
    assert doc.book.isbn == "9784000000002"

    titles = [(s.level, s.title, s.role) for s in doc.sections]
    # 見出しのないまえがきは目次タイトルで節を立てる。囲み記事内の h2「注意」は節にしない
    assert titles == [(1, "まえがき", "front"), (1, "第1章 需要予測", "body"), (2, "1.1 基本", "body")]
    assert doc.stats["toc_matched"] == 3

    by_type = {}
    for b in doc.blocks:
        by_type.setdefault(b.type, []).append(b)
    assert by_type["paragraph"][0].text == "本書は需要予測の入門書です。"
    para = by_type["paragraph"][1]
    assert para.text == "需要予測とは、将来の需要を見積もることです。"
    assert para.annotations == {"ruby": [["需要", "じゅよう"]]}
    assert para.locator.epub.cfi == "epubcfi(/6/4[ch1]!/2/2[ch1]/4)"  # spine 2 番目 → body → section → 2 番目の子
    # 改行の片側が和文なら詰める（和欧間のスペース有無は原文でも揺れるため、検索キーでは空白を無視する）
    assert by_type["paragraph"][2].text == "移動平均はsimple な手法です。"
    assert by_type["note"][0].caption == "注意" and by_type["note"][0].text == "外れ値に弱い。"
    assert by_type["code"][0].language == "python" and "    return" in by_type["code"][0].text
    assert by_type["list"][0].items == ["長所: 簡単", "  計算が軽い", "短所: 遅れる"]
    fig = by_type["figure"][0]
    assert fig.caption == "図1 売上の推移" and fig.asset_id
    assert (res.out_dir / doc.assets[0].path).exists()

    assert (res.out_dir / "doc.md").read_text().startswith("# テスト入門")
    assert res.report["status"] == "ok"

    # 2 回目はキャッシュ
    res2 = ingest(sample_epub, IngestOptions(out_dir=tmp_path / "out"))
    assert res2.skipped == ["extract", "structure", "chunk"]


def test_pdf_ingest(sample_pdf, tmp_path):
    res = ingest(sample_pdf, IngestOptions(out_dir=tmp_path / "out"))
    doc = res.doc
    assert doc.book.language == "ja"
    assert [(s.level, s.title) for s in doc.sections] == [(1, "第1章 需要予測"), (2, "1.1 基本"), (2, "1.2 応用")]
    assert doc.stats["toc_matched"] == 3

    paras = [b for b in doc.blocks if b.type == "paragraph"]
    assert paras[0].text == "需要予測とは、将来の需要を見積もることです。移動平均はもっとも単純な手法で、直近の値を平均します。"
    assert paras[0].page_label == "11"
    # ページをまたぐ段落は連結され、locator は 2 ページ分
    cross = next(p for p in paras if p.text.startswith("この段落"))
    assert cross.text == "この段落は次のページへ続いています。"
    assert [loc.page for loc in cross.locator.pdf] == [1, 2]
    # ノンブルは本文に入らない
    assert not any(b.text in ("11", "12") for b in doc.blocks)

    code = [b for b in doc.blocks if b.type == "code"]
    assert code and code[0].text == "def ma(xs, n):\n    return sum(xs[-n:]) / n"


def test_intake_rejects_unknown_and_drm(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("hello")
    with pytest.raises(IntakeError):
        intake(p)

    drm = tmp_path / "drm.epub"
    with zipfile.ZipFile(drm, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", "<container/>")
        z.writestr("META-INF/rights.xml", "<rights/>")
    with pytest.raises(IntakeError, match="DRM"):
        intake(drm)


def test_compare_epub_vs_pdf(sample_epub, sample_pdf, tmp_path):
    from sensei.compare import compare

    a = ingest(sample_epub, IngestOptions(out_dir=tmp_path / "o")).doc
    b = ingest(sample_pdf, IngestOptions(out_dir=tmp_path / "o")).doc
    rep = compare(a, b)
    assert rep["summary"]["headings_recall_by_level"][1] == 1.0
    assert json.dumps(rep["summary"], ensure_ascii=False)
