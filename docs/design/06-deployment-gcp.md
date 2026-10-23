# 06. GCP（Cloud Run）での運用構成（検討中）

本番は GCP の Cloud Run で動かす。テストはローカル（Docker の PostgreSQL + ローカルフォルダ）で行い、本番との違いは設定（環境変数）だけになるようにする。

本書は検討段階の構成案であり、§5 の未決事項が決まり次第、確定版に更新する。

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
LLM・埋め込み: Vertex AI（Claude、埋め込みモデル）または外部 API
```

| 役割 | GCP のサービス | 理由 |
|---|---|---|
| チャット API | Cloud Run サービス | リクエストに応じて伸び縮みし、待機中の費用が小さい |
| 入稿 | Cloud Run ジョブ | PDF 1 冊で数分、LLM 処理を入れると数十分かかるため、リクエスト型ではなくジョブ型 |
| DB | Cloud SQL for PostgreSQL（pgvector） | 現行スキーマがそのまま動く。データが大きくなったら AlloyDB も選択肢 |
| ファイル | Cloud Storage | 原本、Canonical（doc.json / doc.md / chunks.json）、ページ画像、図版 |
| 入稿のきっかけ | Cloud Storage へのアップロード → Eventarc → ジョブ起動 | 置くだけで入稿が始まる。最初は手動起動でもよい |
| LLM | Vertex AI 上の Claude、または Anthropic API | Vertex なら請求と権限管理を GCP にまとめられる |
| 埋め込み | Vertex AI の埋め込みモデル、または ruri などを自前で運用 | Vertex ならモデルの運用が不要 |
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
| 埋め込み | `hash`（配線確認）や任意のモデル | 本番用モデル（§5） |
| 入稿の起動 | `sensei ingest` / `sensei index` | Cloud Run ジョブ |

## 3. コードの変更点

現行の作りの多くはそのまま使える。必要な変更は次のとおり。

1. **Dockerfile**: 同じイメージをチャット API（サービス）と入稿（ジョブ）で使い分ける。
2. **ファイル置き場の抽象化**: ローカルフォルダと Cloud Storage（`gs://…`）の両方を扱えるようにする（現状はローカルの `out/` 固定）。段ごとのキャッシュ（manifest）も同じ抽象の上で動かす。
3. **DB 接続**: Cloud SQL 向け（Cloud SQL コネクタ、または Unix ソケット）。現行の `SENSEI_DSN` の仕組みは流用する。
4. **埋め込みの提供元に Vertex AI を追加**（`--embedder vertex:<model>`）。
5. **入稿ジョブのエントリポイント**: Cloud Storage 上の原本を受け取り、入稿 → 登録までを 1 回で実行する。
6. **チャット API 本体**: 先生の道具と根拠の検証（[03](03-knowledge-units-and-teacher.md) §4）。

## 4. 運用上の注意

- **入稿の時間**: PyMuPDF の抽出は 500 ページ強で約 3 分（大半が表検出）。LLM による要約・カード抽出を加えると長くなるため、ジョブのタイムアウトとリトライを段単位のキャッシュと組み合わせ、失敗した段から再開できるようにする。
- **メモリ**: 抽出時に PDF 全体と全ページの行情報を保持するため、大きな PDF ではジョブのメモリを多めに取る。
- **同時実行**: 同じ書籍の入稿が重ならないよう、ジョブ起動時に (book_id, version) でロックする。
- **ネットワーク**: Cloud SQL・Vertex AI へは、サービスアカウントの権限で接続する（API キーを埋め込まない）。

## 5. 未決事項

1. **LLM の呼び出し先**: Vertex AI 上の Claude か、Anthropic API か。
2. **埋め込み**: Vertex AI の埋め込みモデル（運用が楽）か、ruri などを自前で Cloud Run に載せるか（日本語特化、ただし運用が増える）。
3. **DB**: Cloud SQL でよいか（推奨）。将来の AlloyDB への移行は、規模を見て判断する。
4. **リージョン**: 東京（asia-northeast1）でよいか。Vertex AI の Claude は、使えるモデルとリージョンの組み合わせに制約があるため、1 を Vertex にする場合は合わせて確認する。

## 6. 次のステップ

未決事項が決まり次第、§3 の 1〜4（Dockerfile、ファイル置き場の抽象化、Cloud SQL 接続、Vertex 埋め込み）から実装する。
