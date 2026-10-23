"""Cloud Run 向けの部品のテスト（GCP には接続しない）。

- Cloud Storage: gcp-storage-emulator（pip）をプロセス内で起動して往復を確かめる
- Vertex AI 埋め込み: google-genai のクライアントを差し替える
"""

import os
import socket
from types import SimpleNamespace

import pytest

from sensei import storage
from sensei.pipeline import IngestOptions, ingest

BUCKET = "sensei-test"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("localhost", 0))
        return s.getsockname()[1]


@pytest.fixture
def gcs(monkeypatch):
    emu = pytest.importorskip("gcp_storage_emulator.server")
    port = _free_port()
    srv = emu.create_server("localhost", port, in_memory=True, default_bucket=BUCKET)
    srv.start()
    monkeypatch.setenv("STORAGE_EMULATOR_HOST", f"http://localhost:{port}")
    monkeypatch.setattr(storage, "_client", None)
    yield f"gs://{BUCKET}"
    srv.stop()
    storage._client = None


def test_ingest_to_gcs_roundtrip_and_cache(gcs, sample_epub):
    raw_uri = f"{gcs}/raw/sample.epub"
    storage._gcs().bucket(BUCKET).blob("raw/sample.epub").upload_from_filename(str(sample_epub))

    res = ingest(raw_uri, IngestOptions(out_dir=f"{gcs}/books"))
    assert str(res.out_dir).startswith(f"{gcs}/books/bk_") and res.out_dir.endswith("/v1/epub")
    names = {b.name for b in storage._gcs().list_blobs(BUCKET, prefix="books/")}
    prefix = res.out_dir[len(f"gs://{BUCKET}/"):]
    for f in ("doc.json", "doc.md", "chunks.json", "manifest.json", "qa.json", "stages/extract.json"):
        assert f"{prefix}/{f}" in names
    assert any(n.startswith(f"{prefix}/assets/") for n in names)

    # 2 回目は Cloud Storage 上の manifest でキャッシュが効き、図版も残る
    res2 = ingest(raw_uri, IngestOptions(out_dir=f"{gcs}/books"))
    assert res2.skipped == ["extract", "structure", "chunk"]
    names2 = {b.name for b in storage._gcs().list_blobs(BUCKET, prefix="books/")}
    assert any(n.startswith(f"{prefix}/assets/") for n in names2)


@pytest.mark.skipif(not os.environ.get("SENSEI_TEST_DSN"), reason="SENSEI_TEST_DSN が未設定")
def test_job_ingest_from_gcs(gcs, sample_epub, monkeypatch, capsys):
    from sensei.cli import main

    storage._gcs().bucket(BUCKET).blob("raw/sample.epub").upload_from_filename(str(sample_epub))
    monkeypatch.setenv("SENSEI_DSN", os.environ["SENSEI_TEST_DSN"])
    rc = main(["job", "ingest", f"{gcs}/raw/sample.epub", "--out", f"{gcs}/books", "--embedder", "hash:32"])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert '"qa": "ok"' in out and '"embeddings":' in out and "gs://sensei-test/books/" in out


def test_vertex_embedder_with_fake_client(monkeypatch):
    pytest.importorskip("google.genai")
    from google import genai

    from sensei.embed import get_embedder

    calls = []

    class FakeModels:
        def embed_content(self, model, contents, config):
            calls.append((model, list(contents), config.task_type, config.output_dimensionality))
            return SimpleNamespace(embeddings=[SimpleNamespace(values=[3.0, 4.0]) for _ in contents])

    monkeypatch.setattr(genai, "Client", lambda **kw: SimpleNamespace(models=FakeModels(), kw=kw))
    emb = get_embedder("vertex:gemini-embedding-001?dim=2")
    assert emb.name == "vertex-gemini-embedding-001-2" and emb.dim == 2
    assert emb.embed_documents(["a", "b", "c"]) == [[0.6, 0.8]] * 3  # 正規化される
    assert emb.embed_query("q") == [0.6, 0.8]
    assert {c[2] for c in calls} == {"RETRIEVAL_DOCUMENT", "RETRIEVAL_QUERY"}
    assert all(len(c[1]) == 1 for c in calls)  # gemini-embedding 系は 1 件ずつ


def test_dsn_from_env_cloud_sql(monkeypatch):
    from sensei.db.store import dsn_from_env

    monkeypatch.delenv("SENSEI_DSN", raising=False)
    monkeypatch.setenv("SENSEI_DB_INSTANCE", "proj:asia-northeast1:sensei")
    monkeypatch.setenv("SENSEI_DB_PASSWORD", "p@ss word")
    dsn = dsn_from_env()
    assert "host=/cloudsql/proj:asia-northeast1:sensei" in dsn and "dbname=sensei" in dsn
    assert "password='p@ss word'" in dsn
