"""埋め込みモデル（差し替え可能）。

指定は "提供元:モデル名" の文字列で行う。

- st:<model>      sentence-transformers（ローカル実行・API キー不要。初回に Hugging Face からモデル取得）
                  日本語の推奨: st:cl-nagoya/ruri-v3-310m（768 次元）, 多言語: st:intfloat/multilingual-e5-large
- vertex:<model>  Vertex AI（本番の既定）。例: vertex:gemini-embedding-001?dim=768
                  認証はサービスアカウント（ローカルは gcloud auth application-default login）。
                  プロジェクト・リージョンは GOOGLE_CLOUD_PROJECT / SENSEI_VERTEX_LOCATION（既定 asia-northeast1）
- voyage:<model>  Voyage AI API（VOYAGE_API_KEY が必要）。例: voyage:voyage-3.5
- hash            文字 n-gram のハッシュ（意味は扱えない）。配線確認・テスト専用

検索文と文書でモデル指定の接頭辞が違うもの（ruri, e5）は PREFIXES で付け分ける。
"""

from __future__ import annotations

import hashlib
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Protocol
from urllib.parse import parse_qsl

PREFIXES: dict[str, tuple[str, str]] = {
    # model 名の部分一致: (検索文の接頭辞, 文書の接頭辞)
    "ruri-v3": ("検索クエリ: ", "検索文書: "),
    "ruri-": ("クエリ: ", "文章: "),
    "e5": ("query: ", "passage: "),
}


class Embedder(Protocol):
    name: str
    dim: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


def _prefixes(model: str) -> tuple[str, str]:
    for key, pre in PREFIXES.items():
        if key in model:
            return pre
    return ("", "")


class HashEmbedder:
    """文字 1〜3-gram を特徴ハッシュで固定次元にする。意味的な類似は捉えない（テスト・配線確認用）。"""

    def __init__(self, dim: int = 256):
        self.name = f"hash-{dim}"
        self.dim = dim

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        s = text.lower()
        for n in (1, 2, 3):
            for i in range(len(s) - n + 1):
                h = int.from_bytes(hashlib.blake2b(s[i : i + n].encode(), digest_size=8).digest(), "little")
                v[h % self.dim] += 1.0 if (h >> 63) & 1 else -1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    def embed_documents(self, texts):
        return [self._vec(t) for t in texts]

    def embed_query(self, text):
        return self._vec(text)


class SentenceTransformerEmbedder:
    def __init__(self, model: str, batch_size: int = 32):
        from sentence_transformers import SentenceTransformer  # 任意依存

        self._m = SentenceTransformer(model)
        self.name = model
        self.dim = self._m.get_sentence_embedding_dimension()
        self._q, self._d = _prefixes(model)
        self._bs = batch_size

    def embed_documents(self, texts):
        return self._m.encode([self._d + t for t in texts], batch_size=self._bs, normalize_embeddings=True).tolist()

    def embed_query(self, text):
        return self._m.encode([self._q + text], normalize_embeddings=True)[0].tolist()


class VoyageEmbedder:
    def __init__(self, model: str, batch_size: int = 64):
        import voyageai  # 任意依存

        self._c = voyageai.Client(api_key=os.environ.get("VOYAGE_API_KEY"))
        self.name = f"voyage-{model}" if not model.startswith("voyage") else model
        self._model = model
        self._bs = batch_size
        self.dim = len(self._c.embed(["dim"], model=model, input_type="query").embeddings[0])

    def embed_documents(self, texts):
        out = []
        for i in range(0, len(texts), self._bs):
            out.extend(self._c.embed(texts[i : i + self._bs], model=self._model, input_type="document").embeddings)
        return out

    def embed_query(self, text):
        return self._c.embed([text], model=self._model, input_type="query").embeddings[0]


class VertexEmbedder:
    """Vertex AI の埋め込み（google-genai SDK）。

    pgvector の HNSW 索引は 2000 次元までなので、出力次元（dim）は既定 768 に落とす。
    1 リクエストあたりの件数（batch）はモデルの上限に合わせて指定する。gemini-embedding 系は既定 1 件で、
    スレッドで並列に投げる。
    """

    def __init__(self, model: str, dim: int = 768, batch: int | None = None, workers: int = 8):
        from google import genai  # 任意依存

        self._client = genai.Client(
            vertexai=True,
            project=os.environ.get("GOOGLE_CLOUD_PROJECT"),
            location=os.environ.get("SENSEI_VERTEX_LOCATION", "asia-northeast1"),
        )
        self._model = model
        self.dim = dim
        self.name = f"vertex-{model}-{dim}"
        self._batch = batch or (1 if model.startswith("gemini-embedding") else 16)
        self._workers = workers

    def _call(self, texts: list[str], task_type: str) -> list[list[float]]:
        from google.genai import types

        cfg = types.EmbedContentConfig(task_type=task_type, output_dimensionality=self.dim, auto_truncate=True)
        for attempt in range(6):
            try:
                resp = self._client.models.embed_content(model=self._model, contents=texts, config=cfg)
                return [_normalize(e.values) for e in resp.embeddings]
            except Exception as e:  # noqa: BLE001 - 429/503 は待って再試行
                if attempt == 5 or not any(k in str(e) for k in ("429", "RESOURCE_EXHAUSTED", "503", "UNAVAILABLE")):
                    raise
                time.sleep(2 ** attempt)
        raise RuntimeError("unreachable")

    def embed_documents(self, texts):
        batches = [texts[i : i + self._batch] for i in range(0, len(texts), self._batch)]
        with ThreadPoolExecutor(self._workers) as ex:
            results = ex.map(lambda b: self._call(b, "RETRIEVAL_DOCUMENT"), batches)
        return [v for r in results for v in r]

    def embed_query(self, text):
        return self._call([text], "RETRIEVAL_QUERY")[0]


def _normalize(v: list[float]) -> list[float]:
    # 次元を落とした出力は正規化されていないことがあるため、コサイン距離用に正規化する
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def get_embedder(spec: str) -> Embedder:
    if spec == "hash" or spec.startswith("hash:"):
        return HashEmbedder(int(spec.split(":", 1)[1]) if ":" in spec else 256)
    provider, _, rest = spec.partition(":")
    model, _, query = rest.partition("?")
    opts = dict(parse_qsl(query))
    if provider == "vertex":
        return VertexEmbedder(
            model, dim=int(opts.get("dim", 768)), batch=int(opts["batch"]) if "batch" in opts else None
        )
    if provider == "st":
        return SentenceTransformerEmbedder(model)
    if provider == "voyage":
        return VoyageEmbedder(model)
    raise ValueError(
        f"unknown embedder: {spec}（例: vertex:gemini-embedding-001?dim=768, st:cl-nagoya/ruri-v3-310m, hash）"
    )


def embedding_text(context_header: str, text: str) -> str:
    """チャンクの埋め込み対象。見出しの経路を前置して文脈を持たせる。"""
    return f"{context_header}\n{text}" if context_header else text
