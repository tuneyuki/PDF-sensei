"""日本語と英語が混在する本文のテキスト正規化。"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable

NORMALIZE_VERSION = "4"  # 正規化規則を変えたら上げる（抽出キャッシュの無効化に使う）

_LIGATURES = {
    "ﬀ": "ff",
    "ﬁ": "fi",
    "ﬂ": "fl",
    "ﬃ": "ffi",
    "ﬄ": "ffl",
    "ﬅ": "st",
    "ﬆ": "st",
}
_LIGATURE_RE = re.compile("|".join(_LIGATURES))
# タブ・改行以外の制御文字と、ゼロ幅文字・ソフトハイフン
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f​-‍⁠﻿­]")


def is_cjk(ch: str) -> bool:
    """和文として扱う文字か（かな・漢字・全角記号・和文句読点）。"""
    if not ch:
        return False
    o = ord(ch)
    return (
        0x3000 <= o <= 0x30FF  # 和文記号・句読点・ひらがな・カタカナ
        or 0x3400 <= o <= 0x4DBF  # CJK 統合漢字拡張 A
        or 0x4E00 <= o <= 0x9FFF  # CJK 統合漢字
        or 0xF900 <= o <= 0xFAFF  # CJK 互換漢字
        or 0xFF00 <= o <= 0xFFEF  # 全角英数・半角カナ
        or 0x20000 <= o <= 0x2FFFF  # CJK 統合漢字拡張 B 以降
    )


def join_lines(lines: list[str], dehyphenate: Callable[[str, str], bool] | None = None) -> str:
    """折り返された行を 1 段落に連結する。

    - 和文同士の境界は詰めて連結（「リポジトリ\\nのすべて」→「リポジトリのすべて」）
    - 行末ハイフンは詰めて連結。dehyphenate(前半, 後半) が True のときだけハイフンを除去
      （"reposi-\\ntory" → "repository"。"fast-\\nforward" は複合語なのでハイフンを残す）
    - それ以外は半角スペースで連結
    """
    out = ""
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if not out:
            out = line
            continue
        prev, nxt = out[-1], line[0]
        if prev == "-" and len(out) >= 2 and out[-2].isalpha() and nxt.isalpha():
            head = re.search(r"[A-Za-z]+-$", out)
            tail = re.match(r"[A-Za-z]+", line)
            if dehyphenate and head and tail and dehyphenate(head.group(0)[:-1], tail.group(0)):
                out = out[:-1] + line
            else:
                out += line
        elif is_cjk(prev) or is_cjk(nxt):
            out += line
        else:
            out += " " + line
    return out


def collapse_whitespace(s: str) -> str:
    """HTML 由来の空白を畳む。改行は join_lines と同じ規則で連結する。"""
    s = s.replace(" ", " ")
    return join_lines(re.split(r"[ \t]*[\r\n]+[ \t]*", s)) if "\n" in s or "\r" in s else re.sub(r"[ \t]+", " ", s).strip()


def normalize_text(s: str) -> str:
    """保存用の正規化（NFC）。NFKC は検索キー生成時だけに使う（search_key）。"""
    s = unicodedata.normalize("NFC", s)
    s = _LIGATURE_RE.sub(lambda m: _LIGATURES[m.group(0)], s)
    s = _CONTROL_RE.sub("", s)
    s = re.sub(r"[ \t]{2,}", " ", s)
    # 和文の句読点・閉じ括弧の直後のスペースは不要（「です。 Git」→「です。Git」）
    s = re.sub(r"(?<=[、。，．！？」』）】]) +", "", s)
    return s.strip()


def search_key(s: str) -> str:
    """検索・突き合わせ用のキー（NFKC・小文字化・空白除去）。"""
    s = unicodedata.normalize("NFKC", s).lower()
    return re.sub(r"\s+", "", s)
