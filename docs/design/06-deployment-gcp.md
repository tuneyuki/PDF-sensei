# 06. GCP（Cloud Run）での運用構成

本番は GCP の Cloud Run で動かす。テストはローカル（Docker の PostgreSQL + ローカルフォルダ）で行い、本番との違いは設定（環境変数）だけになるようにする。

デプロイ手順は [deploy/README.md](../../deploy/README.md)。

## 1. 方針

- チャット API（先生）と入稿パイプラインは、**同じコンテナイメージ** を Cloud Run の「サービス」と「ジョブ」で使い分ける。
- DB は pgvector が使える **Cloud SQL for PostgreSQL**。[05](05-index-and-search.md) のスキーマがそのまま動く。
- 原本・Canonical・画像などのファイルは **Cloud Storage**。
- 本番の検索はベクトル検索を併用する前提のため、検索精度の評価はいったん止める。評価セット（`eval/progit-ja.yaml`）と `sensei eval` は残し、本番構成ができたら同じ方法で測る。

## 2. 構成

```
[ユーザー] ──► Cloud Run サービス（チャット API・先生）
                    │  検索・根拠の取得
                    ▼
              Cloud SQL for PostgreSQL（pgvector）
                    ▲
                    │  登録
[入稿者] ──► Cloud Storage（原本）──► Cloud Run ジョブ（入稿パイプライン）
                                      ・抽出 → 構造化 → チャンク → 埋め込み → 登録
                                      ・成果物（doc.json・画像）は Cloud Storage へ
LLM・埋め込み: Vertex AI（Claude、埋め込みモデル）
```

| 役割 | GCP のサービス | 理由 |
|---|---|---|
| チャット API | Cloud Run サービス | リクエストに応じて伸び縮みし、待機中の費用が小さい |
| 入稿 | Cloud Run ジョブ | PDF 1 冊で数分、LLM 処理を入れると数十分かかるため、リクエスト型ではなくジョブ型 |
| DB | **Cloud SQL for PostgreSQL**（決定。pgvector） | 現行スキーマがそのまま動く。データが大きくなったら AlloyDB も選択肢 |
| ファイル | Cloud Storage | 原本、Canonical（doc.json / doc.md / chunks.json）、ページ画像、図版 |
| 入稿のきっかけ | Cloud Storage へのアップロード → Eventarc → ジョブ起動 | 置くだけで入稿が始まる。最初は手動起動でもよい |
| LLM | **Vertex AI 上の Claude**（決定） | 請求と権限管理を GCP にまとめられる |
| 埋め込み | **Vertex AI の埋め込みモデル**（決定。既定 `gemini-embedding-001`、768 次元） | モデルの運用が不要 |
| 秘密情報 | Secret Manager | DB のパスワード、API キー |
| イメージ | Artifact Registry（Cloud Build でビルド） | |

### 2.1 Cloud Storage の配置（案）

```
gs://<bucket>/raw/{sha256}.{epub|pdf}                     原本（不変）
gs://<bucket>/books/{book_id}/v{version}/{extractor}/     入稿の成果物（現行の out/ 配下と同じ構成）
    manifest.json, stages/extract.json, doc.json, doc.md, chunks.json, qa.json, assets/
```

### 2.2 ローカルと本番の対応

| | ローカル（テスト） | 本番（Cloud Run） |
|---|---|---|
| DB | `docker/compose.yml` の PostgreSQL + pgvector | Cloud SQL for PostgreSQL |
| DB 接続 | `SENSEI_DSN` | Cloud SQL コネクタ（または Unix ソケット）。接続情報は Secret Manager |
| ファイル | ローカルフォルダ（`out/`） | Cloud Storage（`gs://…`） |
| 埋め込み | `hash`（配線確認）や任意のモデル | `vertex:gemini-embedding-001?dim=768` |
| 入稿の起動 | `sensei ingest` / `sensei index` | Cloud Run ジョブ |

## 3. コードの変更点（実装状況）

| # | 変更 | 状態 |
|---|---|---|
| 1 | **Dockerfile**: 同じイメージをチャット API（サービス）と入稿（ジョブ）で使い分ける | 実装済み |
| 2 | **ファイル置き場の抽象化**（`sensei.storage`）: 入力・出力に `gs://` を指定可能。処理はローカルの一時フォルダで行い、前後で成果物を取り寄せ・書き戻す。段ごとのキャッシュ（manifest）も Cloud Storage 上で効く | 実装済み |
| 3 | **Cloud SQL 接続**: `SENSEI_DB_INSTANCE` 等から Unix ソケット（`/cloudsql/…`）の接続文字列を組み立てる。パスワードは Secret Manager から環境変数で渡す | 実装済み |
| 4 | **Vertex AI 埋め込み**（`--embedder vertex:<model>?dim=768`）: タスク種別（文書／検索文）の付け分け、次元の指定、並列実行、429 の再試行 | 実装済み |
| 5 | **入稿ジョブ**（`sensei job ingest <原本…>`）: 入稿 → QA → 登録を 1 回で実行。QA が fail の書籍は登録しない。1 冊ごとに結果を JSON で出力 | 実装済み |
| 6 | **チャット API 本体**: 先生の道具と根拠の検証（[03](03-knowledge-units-and-teacher.md) §4）。LLM は Vertex AI 上の Claude | 未実装 |

検証（GCP には接続せずに実施）:

- Cloud Storage: エミュレータ（`gcp-storage-emulator`）で、`gs://` の原本 → 入稿 → `gs://` への成果物の書き戻し → 2 回目のキャッシュ、を確認（`tests/test_cloud.py`）。
- 入稿ジョブ: エミュレータとローカルの PostgreSQL で、`gs://` の原本から DB 登録まで通ることを確認。
- コンテナ: イメージをビルドし、ジョブとして実行して Pro Git 日本語版 EPUB の入稿・登録が通ることを確認。
- Vertex AI 埋め込み: クライアントを差し替えたテストのみ（実際の API 呼び出しは未確認）。

## 4. 運用上の注意

- **入稿の時間**: PyMuPDF の抽出は 500 ページ強で約 3 分（大半が表検出）。LLM による要約・カード抽出を加えると長くなるため、ジョブのタイムアウトとリトライを段単位のキャッシュと組み合わせ、失敗した段から再開できるようにする。
- **メモリ**: 抽出時に PDF 全体と全ページの行情報を保持するため、大きな PDF ではジョブのメモリを多めに取る。
- **同時実行**: 同じ書籍の入稿が重ならないよう、ジョブ起動時に (book_id, version) でロックする。
- **ネットワーク**: Cloud SQL・Vertex AI へは、サービスアカウントの権限で接続する（API キーを埋め込まない）。

## 5. 決定事項

| 項目 | 決定 |
|---|---|
| LLM | Vertex AI 上の Claude |
| 埋め込み | Vertex AI の埋め込みモデル |
| DB | Cloud SQL for PostgreSQL |
| リージョン | 東京（asia-northeast1）。ただし Vertex AI のモデルが東京で使えない場合は、Vertex の呼び出しだけ別リージョン（Claude は `global` エンドポイントなど）にする |

確認が必要な点: 使う時点で、Vertex AI の埋め込みモデルと Claude が asia-northeast1 で使えるかを確認する（`SENSEI_VERTEX_LOCATION` で切り替え可能）。

## 6. 次のステップ

1. 実際の GCP プロジェクトで入稿ジョブを動かす（deploy/README.md）。
2. チャット API（Cloud Run サービス）: 先生の道具と根拠の検証、Vertex AI 上の Claude の呼び出し。処理の組み立てとレイテンシ目標（TTFT 10 秒以内）は [07](07-answer-latency.md)。
3. 入稿の自動起動（Cloud Storage へのアップロード → Eventarc → Workflows 経由でジョブ起動）。
