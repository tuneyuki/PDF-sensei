from sensei.chunk import ChunkParams, chunk_doc, estimate_tokens
from sensei.fts import query_expr, tokens
from sensei.pipeline import IngestOptions, ingest


def test_tokens_bigram_and_words():
    assert tokens("ブランチを削除") == ["ブラ", "ラン", "ンチ", "チを", "を削", "削除"]
    assert tokens("git-shell を使う") == ["git-shell", "git", "shell", "を使", "使う"]
    assert tokens("Ｇｉｔ") == ["git"]


def test_query_expr_drops_hiragana_only_bigrams():
    expr = query_expr("ファイルを削除したい")
    assert "'した'" not in expr and "'削除'" in expr
    assert query_expr("した") == "'した'"  # それしかなければ残す


def test_estimate_tokens():
    assert estimate_tokens("あいう") == 3
    assert estimate_tokens("abcdefgh") == 2


def test_chunks_respect_sections_and_cover_blocks(sample_epub, tmp_path):
    res = ingest(sample_epub, IngestOptions(out_dir=tmp_path / "out"))
    cs = res.chunks
    assert cs.chunks, "chunks"
    for c in cs.chunks:
        assert c.heading_path and c.context_header.startswith("テスト入門 > ")
        secs = {b.section_id for b in res.doc.blocks if b.block_id in c.block_ids}
        assert secs == {c.section_id}  # 節をまたがない
    assert {"chunk_coverage"} <= {x["check"] for x in res.report["checks"]}
    assert next(x for x in res.report["checks"] if x["check"] == "chunk_coverage")["ok"]


def test_long_block_is_split(sample_epub, tmp_path):
    doc = ingest(sample_epub, IngestOptions(out_dir=tmp_path / "out")).doc
    doc.blocks[-1].text = "長い文です。" * 400
    cs = chunk_doc(doc, ChunkParams(target_tokens=100, max_tokens=200, min_tokens=20))
    assert max(c.token_count for c in cs.chunks) <= 200
