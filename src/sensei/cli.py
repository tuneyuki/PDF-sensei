"""sensei CLI。

    sensei ingest BOOK.pdf [--extractor pymupdf|docling] [--out out]
    sensei compare out/.../doc.json out/.../doc.json
    sensei db init
    sensei index out/{book_id}/v1/{extractor} [--embedder st:cl-nagoya/ruri-v3-310m]
    sensei embed --book BOOK_ID --embedder ...
    sensei search "質問" [--book BOOK_ID] [--mode hybrid|fts|vector] [--embedder ...]
    sensei eval eval/progit-ja.yaml [--book BOOK_ID] [--mode ...] [--embedder ...]

DB の接続先は環境変数 SENSEI_DSN（既定: postgresql://sensei:sensei@localhost:5432/sensei）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="sensei")
    sub = ap.add_subparsers(dest="cmd", required=True)

    ing = sub.add_parser("ingest", help="EPUB/PDF を入稿して Canonical（doc.json / doc.md）を作る")
    ing.add_argument("path")
    ing.add_argument("--out", default="out")
    ing.add_argument("--extractor", default="auto", choices=["auto", "epub", "pymupdf", "docling"])
    ing.add_argument("--book-id")
    ing.add_argument("--version", type=int, default=1)
    ing.add_argument("--no-tables", action="store_true", help="PDF の表検出を省略（高速化）")
    ing.add_argument("--force", action="store_true", help="キャッシュを無視して再実行")

    cmp_ = sub.add_parser("compare", help="同じ書籍の 2 つの doc.json を突き合わせる（抽出器の比較）")
    cmp_.add_argument("a")
    cmp_.add_argument("b")
    cmp_.add_argument("--out", help="Markdown レポートの出力先")

    db = sub.add_parser("db", help="DB の操作")
    db.add_argument("action", choices=["init"])

    idx = sub.add_parser("index", help="入稿の成果物（doc.json / chunks.json）を DB に登録する")
    idx.add_argument("out_dirs", nargs="+")
    idx.add_argument("--embedder", help="例: st:cl-nagoya/ruri-v3-310m, voyage:voyage-3.5, hash")
    idx.add_argument("--no-activate", action="store_true", help="登録だけして公開中バージョンを切り替えない")

    emb = sub.add_parser("embed", help="登録済みの書籍に埋め込みを追加する")
    emb.add_argument("--book", required=True)
    emb.add_argument("--version", type=int)
    emb.add_argument("--embedder", required=True)

    sr = sub.add_parser("search", help="検索")
    sr.add_argument("query")
    sr.add_argument("--book", action="append")
    sr.add_argument("--mode", default="hybrid", choices=["hybrid", "fts", "vector"])
    sr.add_argument("--embedder")
    sr.add_argument("-k", type=int, default=5)

    ev = sub.add_parser("eval", help="検索の評価")
    ev.add_argument("spec")
    ev.add_argument("--book", action="append")
    ev.add_argument("--mode", default="hybrid", choices=["hybrid", "fts", "vector"])
    ev.add_argument("--embedder")
    ev.add_argument("-v", "--verbose", action="store_true")

    args = ap.parse_args(argv)
    if args.cmd in ("db", "index", "embed", "search", "eval"):
        return _db_commands(args)
    if args.cmd == "ingest":
        from .intake import IntakeError
        from .pipeline import IngestOptions, ingest

        try:
            res = ingest(
                args.path,
                IngestOptions(
                    out_dir=Path(args.out), extractor=args.extractor, detect_tables=not args.no_tables,
                    book_id=args.book_id, version=args.version, force=args.force,
                ),
            )
        except IntakeError as e:
            print(f"入稿できません: {e}", file=sys.stderr)
            return 2
        s = res.doc.stats
        print(f"out: {res.out_dir}")
        print(f"title: {res.doc.book.title}  lang: {res.doc.book.language}  pages: {res.doc.book.page_count}")
        print(f"blocks: {s['blocks']} {s['blocks_by_type']}")
        print(f"sections: {s['sections']} by level {s['sections_by_level']} by role {s['sections_by_role']}")
        print(f"toc matched: {s['toc_matched']}/{s['toc_entries']}  chars: {s['chars_total']}")
        print("timings: " + ", ".join(f"{k}={v:.1f}s" for k, v in res.timings.items()) + (f"  (cached: {res.skipped})" if res.skipped else ""))
        print(f"QA: {res.report['status']}")
        for c in res.report["checks"]:
            if not c["ok"]:
                print(f"  - [{c['level']}] {c['check']}={c['value']}: {c['message']}")
        return 0

    if args.cmd == "compare":
        from .compare import compare_files

        report = compare_files(Path(args.a), Path(args.b))
        if args.out:
            Path(args.out).write_text(report["markdown"])
        print(report["markdown"])
        print(json.dumps(report["summary"], ensure_ascii=False))
        return 0
    return 1



def _db_commands(args) -> int:
    from .db import store
    from .embed import get_embedder

    embedder = get_embedder(args.embedder) if getattr(args, "embedder", None) else None
    with store.connect() as conn:
        if args.cmd == "db":
            store.init_schema(conn)
            print("schema ok")
        elif args.cmd == "index":
            for d in args.out_dirs:
                r = store.index_book(conn, Path(d), embedder=embedder, activate=not args.no_activate)
                print(json.dumps(r, ensure_ascii=False))
        elif args.cmd == "embed":
            version = args.version or conn.execute(
                "SELECT active_version FROM books WHERE book_id = %s", (args.book,)).fetchone()[0]
            print(f"embedded {store.embed_book(conn, args.book, version, embedder)} chunks with {embedder.name}")
        elif args.cmd == "search":
            from .search import search

            for i, h in enumerate(search(conn, args.query, k=args.k, mode=args.mode, embedder=embedder,
                                         book_ids=args.book), start=1):
                print(f"{i}. {h.citation}  score={h.score:.4f} {h.ranks}")
                print("   " + h.text[:160].replace("\n", " "))
        elif args.cmd == "eval":
            from .evaluate import run_eval

            r = run_eval(conn, Path(args.spec), mode=args.mode, embedder=embedder, book_ids=args.book)
            print(f"{r['name']} mode={r['mode']} model={r['model']} books={r['books']} n={r['n']}")
            print(f"hit@1={r['hit@1']:.1%} hit@5={r['hit@5']:.1%} hit@10={r['hit@10']:.1%} MRR={r['mrr']:.3f}")
            if args.verbose:
                for q in r["results"]:
                    print(f"  [{q.rank or '-'}] {q.q}  → {q.top}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
