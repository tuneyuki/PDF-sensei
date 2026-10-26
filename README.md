# PDF-sensei

EPUB / PDF の書籍を元に、その本の「先生」として要約・解説・分析・提案を行うチャットボットを作るためのリポジトリ。

## 設計

設計は後勝ち（後から決めた内容を正とする）で更新している。**現在有効な決定は [00. 決定事項の一覧](docs/design/00-decisions.md) にまとめてある。** 個々の文書の古い記述には、後の決定への参照を書き足している。

- [00. 決定事項の一覧（後勝ち）](docs/design/00-decisions.md)
- [01. データ格納形式と入稿パイプライン設計](docs/design/01-data-model-and-ingestion.md)
- [02. 入稿パイプライン MVP 前半の実装と検証（Pro Git）](docs/design/02-ingestion-mvp-validation.md)
- [03. 知識カードと「先生」の設計方針](docs/design/03-knowledge-units-and-teacher.md)
- [04. 共通パイプラインと特徴パッケージ](docs/design/04-architecture-traits.md)
- [05. チャンク分割・登録・検索の実装と評価](docs/design/05-index-and-search.md)
- [06. GCP（Cloud Run）での運用構成](docs/design/06-deployment-gcp.md)（デプロイ手順: [deploy/README.md](deploy/README.md)）
- [07. 回答までの処理とレイテンシ](docs/design/07-answer-latency.md)
- [08. MVP1 の題材と入稿コストの試算](docs/design/08-mvp1-corpus-and-ingest-cost.md)（題材の取得: `scripts/fetch-corpus.sh`）
- [09. レビューの考え方](docs/design/09-review-strategy.md)
- [10. 公開後の修正と版の運用](docs/design/10-post-release-fixes.md)
- [11. 書籍カタログの管理](docs/design/11-catalog-management.md)
- [12. テーブル定義（全体像）](docs/design/12-schema.md)

## 動かし方

```bash
uv venv -p 3.12 .venv && uv pip install -p .venv/bin/python -e '.[dev,st]'

# 入稿: out/{book_id}/v1/{extractor}/ に doc.json（Canonical）・doc.md・chunks.json・qa.json を出力
.venv/bin/sensei ingest path/to/book.epub
.venv/bin/sensei ingest path/to/book.pdf            # PyMuPDF（既定）
.venv/bin/sensei ingest path/to/book.pdf --extractor docling   # 要 pip install '.[docling]'

# 同じ書籍の 2 つの抽出結果を突き合わせる（例: EPUB を正解として PDF 抽出を評価）
.venv/bin/sensei compare out/A/doc.json out/B/doc.json

# DB（ローカルの Docker: PostgreSQL 16 + pgvector）に登録して検索
# 埋め込みの指定は手元で試す例。本番の既定は vertex:gemini-embedding-001?dim=768（docs/design/06）
docker compose -f docker/compose.yml up -d
export SENSEI_DSN=postgresql://sensei:sensei@localhost:5432/sensei
.venv/bin/sensei db init
.venv/bin/sensei index out/bk_xxxx/v1/epub --embedder st:cl-nagoya/ruri-v3-310m
.venv/bin/sensei search "間違って git add したファイルを戻したい" --embedder st:cl-nagoya/ruri-v3-310m
.venv/bin/sensei eval eval/progit-ja.yaml --book bk_xxxx --mode hybrid --embedder st:cl-nagoya/ruri-v3-310m

.venv/bin/python -m pytest                       # DB の結合テストは SENSEI_TEST_DSN を設定したときだけ
```

書籍ファイルと入稿の成果物はリポジトリに入れません（`.gitignore` で除外）。
