"""埋め込みモデル（差し替え可能）。

指定は "提供元:モデル名" の文字列で行う。

- st:<model>      sentence-transformers（ローカル実行・API キー不要。初回に Hugging Face からモデル取得）
                  日本語の推奨: st:cl-nagoya/ruri-v3-310m（768 次元）, 多言語: st:intfloat/multilingual-e5-large
- voyage:<model>  Voyage AI API（VOYAGE_API_KEY が必要）。例: voyage:voyage-3.5
- hash            文字 n-gram のハッシュ（意味は扱えない）。配線確認・テスト専用

検索文と文書でモデル指定の接頭辞が違うもの（ruri, e5）は PREFIXES で付け分ける。
"""

from __future__ import annotations

import hashlib
import math
import os
from typing import Protocol

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


def get_embedder(spec: str) -> Embedder:
    if spec == "hash" or spec.startswith("hash:"):
        return HashEmbedder(int(spec.split(":", 1)[1]) if ":" in spec else 256)
    provider, _, model = spec.partition(":")
    if provider == "st":
        return SentenceTransformerEmbedder(model)
    if provider == "voyage":
        return VoyageEmbedder(model)
    raise ValueError(f"unknown embedder: {spec}（例: st:cl-nagoya/ruri-v3-310m, voyage:voyage-3.5, hash）")


def embedding_text(context_header: str, text: str) -> str:
    """チャンクの埋め込み対象。見出しの経路を前置して文脈を持たせる。"""
    return f"{context_header}\n{text}" if context_header else text
