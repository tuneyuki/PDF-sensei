"""ファイル置き場の抽象化（ローカルフォルダ / Cloud Storage）。

パイプラインは常にローカルの作業フォルダで動かし、置き場が gs:// のときは
実行前に既存の成果物（キャッシュ判定用）を取り寄せ、実行後に書き戻す。
"""

from __future__ import annotations

import posixpath
from pathlib import Path


def is_gcs(uri: str | Path) -> bool:
    return str(uri).startswith("gs://")


def join(base: str | Path, *parts: str) -> str | Path:
    if is_gcs(base):
        return posixpath.join(str(base).rstrip("/"), *parts)
    return Path(base).joinpath(*parts)


def _split(uri: str) -> tuple[str, str]:
    rest = uri[len("gs://"):]
    bucket, _, prefix = rest.partition("/")
    return bucket, prefix.rstrip("/")


_client = None


def _gcs():
    global _client
    if _client is None:
        import os

        from google.cloud import storage  # 任意依存

        if os.environ.get("STORAGE_EMULATOR_HOST"):
            # ローカルのエミュレータ（テスト用）。認証なしで接続する
            from google.auth.credentials import AnonymousCredentials

            _client = storage.Client(project="local", credentials=AnonymousCredentials())
        else:
            _client = storage.Client()
    return _client


def download_file(uri: str, dest: Path) -> Path:
    bucket, name = _split(uri)
    dest.parent.mkdir(parents=True, exist_ok=True)
    _gcs().bucket(bucket).blob(name).download_to_filename(str(dest))
    return dest


def download_prefix(uri: str, dest: Path, exclude_dirs: tuple[str, ...] = ()) -> int:
    """uri 配下のファイルを dest に取り寄せる。exclude_dirs（例: "assets"）配下は除く。"""
    bucket, prefix = _split(uri)
    n = 0
    for blob in _gcs().list_blobs(bucket, prefix=prefix + "/"):
        rel = blob.name[len(prefix) + 1 :]
        if not rel or rel.endswith("/") or rel.split("/", 1)[0] in exclude_dirs:
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        blob.download_to_filename(str(target))
        n += 1
    return n


def delete_prefix(uri: str) -> int:
    bucket, prefix = _split(uri)
    blobs = list(_gcs().list_blobs(bucket, prefix=prefix + "/"))
    for b in blobs:
        b.delete()
    return len(blobs)


def upload_dir(src: Path, uri: str) -> int:
    bucket, prefix = _split(uri)
    b = _gcs().bucket(bucket)
    n = 0
    for p in sorted(src.rglob("*")):
        if p.is_file():
            b.blob(f"{prefix}/{p.relative_to(src).as_posix()}").upload_from_filename(str(p))
            n += 1
    return n
