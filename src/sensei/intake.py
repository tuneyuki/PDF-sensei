"""[0] Intake: ハッシュ計算・形式判定・DRM 検知。"""

from __future__ import annotations

import hashlib
import zipfile
from dataclasses import dataclass
from pathlib import Path


class IntakeError(Exception):
    pass


@dataclass
class IntakeResult:
    path: Path
    sha256: str
    source_format: str  # "epub" | "pdf"
    size_bytes: int

    @property
    def default_book_id(self) -> str:
        # 本番では書籍レジストリで採番（改訂版を同じ book_id に紐付ける）。MVP はハッシュ由来。
        return "bk_" + self.sha256[:12]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def detect_format(path: Path) -> str:
    with path.open("rb") as f:
        head = f.read(64)
    if head.startswith(b"%PDF-"):
        return "pdf"
    if head.startswith(b"PK"):
        with zipfile.ZipFile(path) as z:
            try:
                mimetype = z.read("mimetype").decode("ascii", "replace").strip()
            except KeyError:
                mimetype = ""
            if mimetype == "application/epub+zip" or "META-INF/container.xml" in z.namelist():
                return "epub"
    raise IntakeError(f"未対応の形式です: {path.name}")


def check_drm(path: Path, fmt: str) -> None:
    if fmt == "epub":
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
            if "META-INF/rights.xml" in names:
                raise IntakeError("DRM 付き EPUB（rights.xml）は対象外です")
            if "META-INF/encryption.xml" in names:
                enc = z.read("META-INF/encryption.xml").decode("utf-8", "replace")
                # フォント難読化（IDPF/Adobe）だけなら本文は読めるので許容する
                algos = {a for a in _attr_values(enc, "Algorithm")}
                font_only = {
                    "http://www.idpf.org/2008/embedding",
                    "http://ns.adobe.com/pdf/enc#RC",
                }
                if algos - font_only:
                    raise IntakeError("暗号化された EPUB は対象外です")
    else:
        import pymupdf

        with pymupdf.open(path) as doc:
            if doc.needs_pass:
                raise IntakeError("パスワード付き PDF は対象外です")
            if doc.is_encrypted and not doc.permissions & pymupdf.PDF_PERM_COPY:
                raise IntakeError("テキストのコピーが禁止された PDF は対象外です")


def _attr_values(xml: str, attr: str) -> list[str]:
    import re

    return re.findall(rf'{attr}="([^"]+)"', xml)


def intake(path: str | Path) -> IntakeResult:
    p = Path(path)
    if not p.is_file():
        raise IntakeError(f"ファイルがありません: {p}")
    fmt = detect_format(p)
    check_drm(p, fmt)
    return IntakeResult(path=p, sha256=sha256_file(p), source_format=fmt, size_bytes=p.stat().st_size)
