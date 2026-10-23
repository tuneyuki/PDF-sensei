"""sensei CLI。

    sensei ingest BOOK.pdf [--extractor pymupdf|docling] [--out out]
    sensei compare out/.../doc.json out/.../doc.json
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

    args = ap.parse_args(argv)
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


if __name__ == "__main__":
    sys.exit(main())
