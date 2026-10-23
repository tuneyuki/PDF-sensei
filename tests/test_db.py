"""DB を使う結合テスト。SENSEI_TEST_DSN が設定されているときだけ実行する。

    docker compose -f docker/compose.yml up -d
    SENSEI_TEST_DSN=postgresql://sensei:sensei@localhost:5432/sensei pytest tests/test_db.py
"""

import os

import pytest

from sensei.embed import HashEmbedder
from sensei.pipeline import IngestOptions, ingest

DSN = os.environ.get("SENSEI_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="SENSEI_TEST_DSN が未設定")


def test_index_and_search(sample_epub, tmp_path):
    from sensei.db import store
    from sensei.search import search

    res = ingest(sample_epub, IngestOptions(out_dir=tmp_path / "out"))
    emb = HashEmbedder(64)
    with store.connect(DSN) as conn:
        store.init_schema(conn)
        r = store.index_book(conn, res.out_dir, embedder=emb)
        assert r["chunks"] == len(res.chunks.chunks) and r["embeddings"] == r["chunks"]
        # 再登録しても重複しない
        store.index_book(conn, res.out_dir, embedder=emb)
        n = conn.execute("SELECT count(*) FROM chunks WHERE book_id = %s", (r["book_id"],)).fetchone()[0]
        assert n == r["chunks"]

        hits = search(conn, "移動平均", mode="fts", book_ids=[r["book_id"]])
        assert hits and hits[0].heading_path[-1] == "1.1 基本"
        assert hits[0].citation.startswith("『テスト入門』第1章 需要予測 > 1.1 基本")
        assert search(conn, "移動平均", mode="hybrid", embedder=emb, book_ids=[r["book_id"]])

        assert store.embed_book(conn, r["book_id"], 1, HashEmbedder(32)) == r["chunks"]
