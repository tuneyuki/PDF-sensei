"""全文検索用のトークン化。

PostgreSQL 標準の全文検索（tsvector, 'simple' 設定）で日本語を扱うため、和文は文字 bigram、
英数字は小文字化した単語に分けてから渡す。拡張（PGroonga / pg_bigm）がなくても動く。
規模が大きくなったら PGroonga に切り替える想定（docs/design/01）。
"""

from __future__ import annotations

import re
import unicodedata

from .textnorm import is_cjk

_WORD_RE = re.compile(r"[a-z0-9][a-z0-9_\-\.]*[a-z0-9]|[a-z0-9]")


def tokens(text: str) -> list[str]:
    s = unicodedata.normalize("NFKC", text).lower()
    out: list[str] = []
    run: list[str] = []

    def flush():
        if len(run) == 1:
            out.append(run[0])
        else:
            out.extend(run[i] + run[i + 1] for i in range(len(run) - 1))
        run.clear()

    i = 0
    while i < len(s):
        ch = s[i]
        if is_cjk(ch) and not unicodedata.category(ch).startswith("P") and ch not in "　・ー":
            run.append(ch)
            i += 1
            continue
        if ch == "ー" and run:  # 長音はカタカナ語の一部として扱う
            run.append(ch)
            i += 1
            continue
        if run:
            flush()
        m = _WORD_RE.match(s, i)
        if m:
            w = m.group(0)
            out.append(w)
            # "git-shell" や "config.txt" は分割した語でも引けるようにする
            if any(c in w for c in "-_."):
                out.extend(p for p in re.split(r"[\-_.]", w) if p)
            i = m.end()
        else:
            i += 1
    if run:
        flush()
    return out


def document_text(text: str) -> str:
    """to_tsvector('simple', ...) に渡す文字列。"""
    return " ".join(tokens(text))


_HIRAGANA_ONLY = re.compile(r"^[\u3041-\u309fー]+$")


def query_expr(text: str) -> str | None:
    """to_tsquery('simple', ...) に渡す式（OR 結合。順位付けは ts_rank_cd に任せる）。

    ひらがなだけの bigram（「した」「ます」「ない」など）は機能語でほぼ全文書に現れ、
    順位を乱すので検索語から外す（それしか残らない場合は残す）。
    """
    toks = list(dict.fromkeys(tokens(text)))
    content = [t for t in toks if not _HIRAGANA_ONLY.match(t)]
    toks = content or toks
    if not toks:
        return None
    return " | ".join("'" + t.replace("\\", "\\\\").replace("'", "''") + "'" for t in toks)
