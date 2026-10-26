# 00. 決定事項の一覧（後勝ち）

## 運用の約束: 後勝ち

設計は会話と検討を重ねながら更新している。文書どうしで記述が食い違うときは、**後から伝えた内容・後から検討した内容を正とする**（後勝ち）。

- 食い違いが見つかったら、古い文書の該当箇所に「後の決定」として新しい文書への参照を書き足す。古い記述は経緯として残し、消さない。
- 現在有効な決定は本書（§1）にまとめる。個々の文書を読む前に、まず本書を見る。
- 実装は文書に追いついていないことがある。文書で決まり、実装がまだのものは §3 に載せる。

## 1. 現在有効な決定

| 項目 | 現在の決定 | 決めた文書 | 置き換えた記述 |
|---|---|---|---|
| 対象 | どのジャンルの本も扱う。共通の土台 + 特徴パッケージ（本を「特徴の組み合わせ」で捉える） | [04](04-architecture-traits.md) | [03](03-knowledge-units-and-teacher.md) のジャンル別の考え方を一般化 |
| 中間表現 | Canonical（DocTree）を正とし、チャンク・埋め込みはいつでも作り直す | [01](01-data-model-and-ingestion.md) | — |
| PDF の抽出 | 既定は PyMuPDF + ヒューリスティック（テキスト PDF）。写真主体・雑誌は画像を Claude に読ませる抽出。Docling は未検証 | [02](02-ingestion-mvp-validation.md), [03](03-knowledge-units-and-teacher.md) | [01](01-data-model-and-ingestion.md) の「Docling を第一候補」 |
| 知識の持ち方 | 知識カード（`units`）。属性の出所を `book` / `inferred` / `external` で区別し、出典として示すのは本の原文だけ | [03](03-knowledge-units-and-teacher.md) | [01](01-data-model-and-ingestion.md) の `concepts` テーブル |
| 本番の基盤 | GCP: Cloud Run（チャット API はサービス、入稿はジョブ）、Cloud SQL for PostgreSQL（pgvector）、Cloud Storage、リージョンは東京 | [06](06-deployment-gcp.md) | [01](01-data-model-and-ingestion.md) の S3・SQS 等の例 |
| 環境 | **ステージングと本番の 2 環境**。入稿・レビューはステージングで行い、本番へは書籍（版）単位で昇格する | [10](10-post-release-fixes.md), [06](06-deployment-gcp.md) §2.3 | [06](06-deployment-gcp.md) の 1 環境の構成 |
| LLM | Vertex AI 上の Claude | [06](06-deployment-gcp.md) | — |
| 埋め込み | Vertex AI の埋め込みモデル（既定 `gemini-embedding-001`、768 次元）。モデルごとに別行で持ち、並べて比較できる | [06](06-deployment-gcp.md), [05](05-index-and-search.md) | [05](05-index-and-search.md) の ruri 推奨、[01](01-data-model-and-ingestion.md) の `vector(1024)` 固定 |
| 全文検索 | 和文は文字 bigram を PostgreSQL 標準の全文検索で扱う（拡張なし）。順位付けは `ts_rank` | [05](05-index-and-search.md) | [01](01-data-model-and-ingestion.md) の PGroonga / pg_bigm |
| 検索精度の評価 | いったん止める。本番構成（ベクトル検索あり）ができたら同じ評価セットで測る | [06](06-deployment-gcp.md) | [05](05-index-and-search.md) の手元での測定依頼 |
| 回答までの処理 | 案 B（整理案）: 回答前の LLM 呼び出しは 0〜1 回、検証は回答後。並列化は計測で必要と分かった箇所だけ | [07](07-answer-latency.md) | [03](03-knowledge-units-and-teacher.md) §4.2 の段ごとの LLM 呼び出し |
| 根拠の受け渡し | Claude の引用機能（Citations）を使う。Vertex AI でも使える。ただし出力形式の固定（structured outputs）とは併用できない | [07](07-answer-latency.md) | [03](03-knowledge-units-and-teacher.md) §4.2 の「利用を検討する」 |
| レビュー | 全件は見ない。例外キュー・抜き取り・質問テストで版ごとに受け入れを判定。個別の修正は `overrides.json` | [09](09-review-strategy.md) | [03](03-knowledge-units-and-teacher.md) の「1 冊 1 時間の人手確認」 |
| 書籍カタログ | 本ごとに書誌情報・権利と公開期間・キャンペーン・サンプルプロンプトを持ち、一覧で管理する。カタログは版に属さず、入稿は初期値を提案するだけで人の編集が勝つ。公開できるかの判定は 1 か所（`visible_books`）にまとめる | [11](11-catalog-management.md) | — |
| キャンペーン | 本ごとの真偽値フラグではなく、期間・並び順・文言を持つ独立したレコードにして本を紐付ける | [11](11-catalog-management.md) §2.3 | — |
| 公開後の修正 | ①止める ②個別に直す ③仕組みを直す ④原本が変わる、の 4 経路 | [10](10-post-release-fixes.md) | — |
| `book_id` | ULID。初回入稿で発行し、改訂版は同じ `book_id` の新しい版。原本のハッシュは版ごとに持つ | [10](10-post-release-fixes.md) §8 | [02](02-ingestion-mvp-validation.md) の実装（ハッシュ由来） |
| ブロックの対応付け | 通し番号の `block_id` とは別に、内容から作る **安定キー（`anchor`）** を持ち、上書きの持ち越しはこのキーで行う | [09](09-review-strategy.md) §5.1 | [09](09-review-strategy.md) §5 / [10](10-post-release-fixes.md) §6.3 の「根拠ブロックは変わりにくい」 |
| 「止める」の反映順 | `overrides.json` を先に書き（正とする）、次に DB。両者の差は突き合わせ処理で直す | [10](10-post-release-fixes.md) §2.2 | 同節の「1 つの操作で同時に」 |
| 個別修正の版管理 | 個別修正（②）でも同じ版を上書きせず、**登録の改訂番号**を上げる。巻き戻しは前の改訂を登録し直す | [10](10-post-release-fixes.md) §3 | 同節の「同じ版に登録し直す」 |
| コストの前提 | Claude の単価は [08](08-mvp1-corpus-and-ingest-cost.md) §3 の表（直販価格）。Sonnet 5.5・Haiku 5.5 も既定で thinking が有効。Haiku 5.5 は入力 10 万トークン超で単価 5 倍。Vertex 上の価格は着手前に確認 | [08](08-mvp1-corpus-and-ingest-cost.md) | — |

## 2. 未決事項（集約）

各文書の未決事項のうち、現時点で開いているもの。

| 項目 | 文書 |
|---|---|
| 一般知識での補足を許すか、回答に載せる本文の量、根拠の見せ方、外部サービス連携、最新情報の確認、広告の扱い、現在地の利用、検証用の雑誌、ネタバレ回避、ロールプレイ、翻訳の扱い | [03](03-knowledge-units-and-teacher.md) §8 |
| 画像抽出の適用範囲、段ごとのモデル割り当て、`en-d2l.pdf` を含めるか | [08](08-mvp1-corpus-and-ingest-cost.md) §7 |
| 受け入れ基準の値、本の区分の決め方、検証に使うモデル | [09](09-review-strategy.md) §9 |
| 前の版を残す期間、①を実行できる人、対応時間の目標、利用者への告知、止めた内容を過去の会話でも隠すか | [10](10-post-release-fixes.md) §10 |
| 公開期間が終わった本の過去の会話、利用者の区分による出し分け、書誌情報の外部取得、管理画面の時期と権限、キャンペーンの表示場所 | [11](11-catalog-management.md) §7 |
| ステージングと本番の分け方（プロジェクトを分けるか、インスタンスを分けるか）、昇格時に埋め込みを成果物に含めて運ぶか本番で計算し直すか | [06](06-deployment-gcp.md) §2.3 |
| Vertex AI 上の Claude と埋め込みの単価・東京リージョンでの提供、Vertex のバッチ予測が Claude に使えるか | [06](06-deployment-gcp.md), [08](08-mvp1-corpus-and-ingest-cost.md) |

## 3. 実装待ち（文書では決定済み、コードは未対応）

実装は現在止めている。再開時にまとめて対応する。

| 項目 | 内容 | 根拠 |
|---|---|---|
| `book_id` を ULID に | 書籍レジストリ（ステージング DB）での発行・照会、`--book-id` 指定、同じハッシュの重複入稿の通知 | [10](10-post-release-fixes.md) §8 |
| ブロックの安定キー | `structure.py` で `anchor` を付与、`blocks` テーブルに列を追加 | [09](09-review-strategy.md) §5.1 |
| 登録の改訂番号 | `book_versions` に改訂番号を追加し、同じ版の再登録で上げる。前の改訂を残す | [10](10-post-release-fixes.md) §3 |
| 版の状態 `accepted` と「止める」 | 状態の追加、チャンク・カードの停止フラグと検索での除外、操作ログのテーブル | [10](10-post-release-fixes.md) §2, §6 |
| ステージング→本番の昇格 | 成果物から本番 DB への `sensei index`（書籍単位）と、その手順 | [06](06-deployment-gcp.md) §2.3 |
| トークン数の概算 | `chunk.py` は和文 1 字 ≒ 1 トークン、[08](08-mvp1-corpus-and-ingest-cost.md) は 1.5 字 ≒ 1 トークン。少数のサンプルで実測し、どちらかに揃える | [08](08-mvp1-corpus-and-ingest-cost.md) §2 |
| `scripts/fetch-corpus.sh` | 取得失敗時に終了コードを 0 以外にする。保存したファイルの先頭（`%PDF-` / `PK`）を検査し、エラーページの保存を防ぐ | 本書の見直し（2026-10-25） |
| 書籍カタログ | `books` の拡張と、寄与者・権利・公開期間・キャンペーン・サンプルプロンプト・変更記録のテーブル、`visible_books` ビュー、CSV での書き出し・取り込み（`sensei catalog`）、承認済みサンプルプロンプトの質問テストへの自動追加 | [11](11-catalog-management.md) |
| LLM 呼び出しの thinking 設定 | Sonnet 5.5・Haiku 5.5 は既定で thinking が有効。軽い作業（文脈ヘッダ・検証）では明示的に抑える | [08](08-mvp1-corpus-and-ingest-cost.md) §4 |
