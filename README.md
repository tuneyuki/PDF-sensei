# PDF-sensei

EPUB / PDF の書籍を元に、その本の「先生」として要約・解説・分析・提案を行うチャットボットを作るためのリポジトリ。

## 設計

- [01. データ格納形式と入稿パイプライン設計](docs/design/01-data-model-and-ingestion.md)
- [02. 入稿パイプライン MVP 前半の実装と検証（Pro Git）](docs/design/02-ingestion-mvp-validation.md)

## 入稿パイプライン（MVP 前半）

```bash
uv venv -p 3.12 .venv && uv pip install -p .venv/bin/python -e '.[dev]'

# 入稿: out/{book_id}/v1/{extractor}/ に doc.json（Canonical）・doc.md・qa.json を出力
.venv/bin/sensei ingest path/to/book.epub
.venv/bin/sensei ingest path/to/book.pdf            # PyMuPDF（既定）
.venv/bin/sensei ingest path/to/book.pdf --extractor docling   # 要 pip install '.[docling]'

# 同じ書籍の 2 つの抽出結果を突き合わせる（例: EPUB を正解として PDF 抽出を評価）
.venv/bin/sensei compare out/A/doc.json out/B/doc.json

.venv/bin/python -m pytest
```

書籍ファイルと入稿の成果物はリポジトリに入れません（`.gitignore` で除外）。
