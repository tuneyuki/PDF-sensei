"""検索の評価（質問 → 正解の見出し）。

指標: hit@1 / hit@5 / hit@10（上位 k 件に正解の節が入った割合）と MRR（最初の正解の順位の逆数の平均）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from .search import Hit, search
from .textnorm import search_key


@dataclass
class QResult:
    q: str
    rank: int | None  # 最初に正解が出た順位（なければ None）
    top: list[str]


def _matches(heading_path: list[str], expected: str) -> bool:
    keys = [search_key(h) for h in heading_path]
    return all(search_key(part) in keys for part in expected.split(" > "))


def first_hit_rank(hits: list[Hit], expected: list[str]) -> int | None:
    for i, h in enumerate(hits, start=1):
        if any(_matches(h.heading_path, e) for e in expected):
            return i
    return None


def run_eval(conn, path: Path, mode: str = "hybrid", embedder=None, book_ids=None, k: int = 10) -> dict:
    spec = yaml.safe_load(path.read_text())
    results: list[QResult] = []
    for item in spec["questions"]:
        hits = search(conn, item["q"], k=k, mode=mode, embedder=embedder, book_ids=book_ids)
        rank = first_hit_rank(hits, item["expected"])
        results.append(QResult(item["q"], rank, [" > ".join(h.heading_path[-2:]) for h in hits[:3]]))
    n = len(results) or 1

    def hit_at(x):
        return round(sum(1 for r in results if r.rank and r.rank <= x) / n, 3)

    return {
        "name": spec.get("name"),
        "mode": mode,
        "model": getattr(embedder, "name", None),
        "books": book_ids,
        "n": len(results),
        "hit@1": hit_at(1),
        "hit@5": hit_at(5),
        "hit@10": hit_at(10),
        "mrr": round(sum(1 / r.rank for r in results if r.rank) / n, 3),
        "results": results,
    }
