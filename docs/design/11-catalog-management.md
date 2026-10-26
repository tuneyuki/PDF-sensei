# 11. 書籍カタログの管理

書籍ごとに、作品タイトル・著者・出版社などの書誌情報、公開期間、おすすめなどのキャンペーン、利用者向けのサンプルプロンプト（「夏バテに効くメニューを提案して」など）を共通のレコードとして持ち、一覧で管理できるようにする。

## 1. 基本方針

### 1.1 「中身」と「カタログ」を分ける

| | 中身（コンテンツ） | カタログ |
|---|---|---|
| 何か | 入稿の成果物（Canonical・チャンク・知識カード）と、その版 | 書誌情報・公開期間・キャンペーン・サンプルプロンプトなど、本の「見せ方・売り方」 |
| 単位 | 版（`book_id` + `version`） | 本（`book_id`）。版をまたいで 1 つ |
| 作り方 | 入稿パイプラインが生成し、人は仕組みを直す（[09](09-review-strategy.md)） | 人が編集する。入稿は初期値を提案するだけ |
| 変わる頻度 | 入稿・改訂のとき | 随時（キャンペーンは週・月単位） |

カタログは版に属さないので、改訂版を公開してもカタログの設定（公開期間・キャンペーン・プロンプト）はそのまま引き継がれる。

### 1.2 入稿は提案、人の編集が勝つ

書誌情報の多くは入稿時に取れる（EPUB の OPF、PDF のメタデータ、ISBN からの書誌検索）。これを **初期値として提案** し、人が編集した項目は以後の入稿で上書きしない（後勝ち。人の編集が後）。

- 項目ごとに出所（`ingest` / `manual`）を記録する。
- 入稿で取れた値が、人が編集した値と違う場合は上書きせず、例外キュー（[09](09-review-strategy.md) §3.1）に「書誌の差分」として出す。

### 1.3 公開できるかは 1 か所で判定する

ある本を利用者に見せてよいかは、複数の条件の組み合わせで決まる。判定を 1 つの関数（DB のビュー）にまとめ、チャット API・一覧・検索のすべてがそれを使う。

```
公開中 = 公開期間の中にある
       ∧ 権利の期間の中にある
       ∧ 公開中の版がある（books.active_version）かつ受け入れ判定に合格している
       ∧ 止められていない（[10](10-post-release-fixes.md) ①）
       ∧ カタログの状態が published
```

## 2. 持つ情報

### 2.1 書誌情報

| 項目 | 例 | 備考 |
|---|---|---|
| 作品タイトル / サブタイトル | 戦争と平和 / 第一巻 | |
| タイトルの読み | せんそうとへいわ | 一覧の並べ替え・検索用 |
| 原題 | Война и мир | 翻訳作品 |
| 寄与者 | トルストイ（著者）、○○（訳者）、○○（監修） | 役割付きで複数。著者だけでなく訳者・編者・イラストも |
| 出版社 / レーベル | | |
| シリーズ / 巻数 | ホームズ全集 / 1 | 雑誌は号（[03](03-knowledge-units-and-teacher.md) の `series_id`） |
| ISBN | 紙・電子それぞれ | ない本もある |
| 発行日 | | 雑誌は発行号の年月 |
| 言語 | ja | |
| ジャンル・特徴 | 文学 / narrative, translated | [04](04-architecture-traits.md) の特徴の組み合わせ（入稿の Profile 段の結果を初期値にする） |
| 紹介文 | | 一覧・本の詳細画面に出す |
| 表紙画像 | | 入稿で取れれば初期値 |
| 対象年齢・注意書き | | 任意 |

### 2.2 権利と公開期間

公開期間は、出版社との契約（権利の期間）と、運用上の公開期間の 2 段で持つ。

| 項目 | 内容 |
|---|---|
| 権利の期間 | 契約で使ってよい期間。権利者、許諾の範囲（本文の引用の可否・量など）も記録する |
| 公開期間 | 実際に利用者に見せる期間。**権利の期間の内側でなければ保存できない** ようにする |
| 公開期間は複数持てる | 期間限定の公開、一時的な取り下げと再公開などのため |

権利の期間が近く切れる本（例: 30 日以内）は一覧で警告する。

### 2.3 キャンペーン

「おすすめ」「新着」「特集」などを本ごとの真偽値（フラグ）で持つと、期間・並び順・文言を表せず、種類が増えるたびに列が増える。そこで **キャンペーンを独立したレコード** にし、本を紐付ける。

| 項目 | 内容 |
|---|---|
| キャンペーン | 名前、種類（おすすめ / 新着 / 特集 / 期間限定 など）、期間、表示する場所 |
| 対象の本 | 並び順、本ごとの見出し・紹介文（「夏に読みたい一冊」など） |

- 常設の「おすすめ」も、期間なしのキャンペーンとして同じ仕組みで扱う。
- 公開中でない本は、キャンペーンに入っていても表示しない（§1.3 の判定を通す）。

### 2.4 サンプルプロンプト

本の画面に、利用者が押すだけで質問できる例文を出す。

| 項目 | 内容 |
|---|---|
| 本文 | 「夏バテに効くメニューを提案して」「ピエールってどういう人物？」 |
| 種類 | 要約 / 解説 / 推薦 / 当てはめ など（[03](03-knowledge-units-and-teacher.md) のユースケース） |
| 表示期間 | 季節もの（夏バテ、紅葉）は期間を付ける。期間外は出さない |
| 並び順・状態 | 下書き / 承認済み。承認済みだけを出す |
| 出所 | 自動生成 / 人が作成 |

作り方:

1. **入稿時に自動で候補を作る。** 特徴パッケージの定番の使い方と、LLM による使い方の提案（[04](04-architecture-traits.md) §5、パイプラインの [5.95]）から候補を出す。
2. **人が選んで承認する。** 季節もの・キャンペーンに合わせたものは人が足す。
3. **サンプルプロンプトは必ず答えられるものにする。** 承認済みのプロンプトを、その本の質問テスト（[09](09-review-strategy.md) §3.3）に自動で加える。新しい版で答えられなくなったら、受け入れ判定の段階で検出する。

### 2.5 状態

| 状態 | 意味 |
|---|---|
| `draft` | 登録したが、まだ公開の準備中（中身がない段階でも作れる。発売前の予約登録など） |
| `ready` | 公開できる状態（受け入れ判定に合格した版がある） |
| `published` | 公開する（実際に見えるかは §1.3 の判定） |
| `suspended` | 一時停止（権利の申し立てなど。[10](10-post-release-fixes.md) ① の本単位の停止と連動） |
| `retired` | 提供終了。会話履歴の表示のために記録は残す |

## 3. データ構造（案）

既存の `books` テーブル（[05](05-index-and-search.md)）をカタログの中心に広げる。

```sql
-- 本（カタログの中心）。book_id は ULID（[10] §8）
ALTER TABLE books ADD COLUMN ... ;   -- 下記の項目を追加
--   subtitle, title_kana, original_title, publisher, imprint, series_id, series_no,
--   isbn_print, isbn_ebook, published_on, genre, traits text[], description,
--   cover_asset, age_rating, notes, catalog_status, field_sources jsonb,
--   updated_at, updated_by

CREATE TABLE book_contributors (      -- 著者・訳者・監修など
  book_id text REFERENCES books, ord int, name text, name_kana text,
  role text,                          -- author | translator | editor | supervisor | illustrator
  PRIMARY KEY (book_id, ord)
);

CREATE TABLE book_rights (            -- 契約上の権利
  rights_id text PRIMARY KEY, book_id text REFERENCES books,
  holder text, starts_at timestamptz, ends_at timestamptz,
  quote_policy text,                  -- 本文の引用の可否・量（[03] §8 の未決事項 3 と連動）
  contract_ref text, notes text
);

CREATE TABLE book_publications (      -- 公開期間（複数可。権利の期間の内側に限る）
  publication_id text PRIMARY KEY, book_id text REFERENCES books,
  starts_at timestamptz, ends_at timestamptz, reason text
);

CREATE TABLE campaigns (
  campaign_id text PRIMARY KEY, name text,
  kind text,                          -- recommended | new | featured | limited ...
  starts_at timestamptz, ends_at timestamptz, placement text, status text
);
CREATE TABLE campaign_books (
  campaign_id text REFERENCES campaigns, book_id text REFERENCES books,
  ord int, headline text, blurb text,
  PRIMARY KEY (campaign_id, book_id)
);

CREATE TABLE book_sample_prompts (
  prompt_id text PRIMARY KEY, book_id text REFERENCES books,
  text text, intent text, ord int, locale text,
  starts_at timestamptz, ends_at timestamptz,   -- 季節もの
  status text,                        -- draft | approved | retired
  source text                         -- auto | manual
);

CREATE TABLE catalog_changes (        -- 変更の記録（誰が・いつ・何を・なぜ）
  change_id bigserial PRIMARY KEY, at timestamptz DEFAULT now(), actor text,
  entity text, entity_id text, before jsonb, after jsonb, reason text
);

-- 公開できるかの判定（§1.3）。チャット API・一覧・検索はこのビューだけを見る
CREATE VIEW visible_books AS ... ;
```

時刻は `timestamptz` で持ち、運用・表示は日本時間で行う。

## 4. 一覧での管理

### 4.1 一覧で見たいもの

| 列・絞り込み | 用途 |
|---|---|
| タイトル・著者・出版社・シリーズ | 探す |
| 状態、公開中かどうか（§1.3 の判定結果と、公開できない理由） | 「なぜ見えないのか」がすぐ分かるようにする（期間外、権利切れ、受け入れ判定待ち、停止中…） |
| 公開中の版、受け入れ判定の結果、QA の状態 | 中身の状態 |
| 公開期間・権利の期間（**期限が近いものを警告**） | 契約の管理 |
| 参加中のキャンペーン | 販促の管理 |
| 承認済みのサンプルプロンプトの数（0 件を警告） | 本の画面の品質 |
| 未処理の例外（書誌の差分など） | 作業の残り |

### 4.2 編集のしかた

- **最初は表計算での一括編集を主にする。** 一覧を CSV / スプレッドシート形式で書き出し、編集して取り込む。取り込みでは検証（公開期間が権利の期間の内側か、必須項目、日付の前後関係など）を行い、変更は `catalog_changes` に記録する。
- 管理画面は、運用の形が固まってから作る（[09](09-review-strategy.md) §8 のレビュー画面と同じ方針）。
- コマンドの例（実装は未着手）: `sensei catalog export` / `sensei catalog import` / `sensei catalog show <book_id>`。

## 5. ステージングと本番での扱い

カタログは、項目によって「どちらの環境で編集するか」が違う（[06](06-deployment-gcp.md) §2.3）。

| 項目 | 編集する環境 | 本番への反映 |
|---|---|---|
| 書誌情報、サンプルプロンプト（自動生成・承認） | ステージング | 版の昇格と一緒に反映する。本番で直した場合はステージングに書き戻す |
| 権利・公開期間・キャンペーン・状態 | **本番**（運用の操作） | 本番が正。ステージングへは参考として複製する |

- 公開期間やキャンペーンの切り替えは、中身の昇格を待たずに本番で即時に行えるようにする。
- 本で 1 つの `book_id`（ULID）で両環境をそろえる（[10](10-post-release-fixes.md) §8）。カタログのレコードは、ステージングでの初回入稿時（または発売前の予約登録時）に作る。

## 6. チャットボットとの関係

- **本の選択**: 利用者が選べる本は `visible_books` に入っているものだけ。
- **公開期間が終わった本の会話**: 新しい質問は受け付けない。過去の会話の表示をどうするかは未決（[10](10-post-release-fixes.md) §9 と合わせて決める）。
- **サンプルプロンプト**: 本の画面に、表示期間内・承認済みのものを並び順に出す。押すとそのまま質問として送る。
- **引用の量**: 権利の許諾範囲（`quote_policy`）に応じて、回答に載せる本文の量を変えられるようにする（[03](03-knowledge-units-and-teacher.md) §8 の未決事項 3）。

## 7. 未決事項

1. **公開期間が終わった本の過去の会話を見せるか。** 権利の条件によっては見せられない。
2. **利用者の区分による出し分け**（無料・有料プランなど）を持つか。持つなら公開期間を区分ごとに持つ。
3. **書誌情報の外部取得**（ISBN から書誌データベースを引く）を使うか。使う場合の取得元。
4. **管理画面を作る時期と、誰が操作するか**（編集者・営業・運用担当で権限を分けるか）。
5. **キャンペーンの表示場所の種類**（トップ画面、ジャンル別、検索結果など）。
