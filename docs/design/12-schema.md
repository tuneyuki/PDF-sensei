# 12. テーブル定義（全体像）

ここまでの決定（[00](00-decisions.md)、後勝ち）をすべて反映した DB のテーブル定義。各文書に散らばっていた定義（[01](01-data-model-and-ingestion.md)・[03](03-knowledge-units-and-teacher.md)・[05](05-index-and-search.md)・[11](11-catalog-management.md)）は本書で置き換える。

> 本書は設計。実装（`src/sensei/db/schema.sql`）は [05](05-index-and-search.md) 時点の 6 テーブルのままで、本書への移行は実装再開時に行う（§6）。

## 1. 全体像

```
                          ┌─────────────── カタログ（本ごと・人が編集） ───────────────┐
 series ◄── books ──┬── book_contributors      book_rights ── book_publications（free/paid）
                    ├── book_flags ──► flag_definitions
                    ├── book_sample_prompts
                    └── catalog_changes（変更記録）
                          └──────────────────────────────────────────────────────────┘
                    │
                    │ active_content_id（公開中の中身）
                    ▼
          ┌──────── 中身（版・改訂ごと・入稿が生成） ────────┐
 book_versions ── book_contents（改訂）── sections
                                       ├── blocks（anchor 付き）
                                       ├── chunks ── chunk_embeddings
                                       ├── summaries ── summary_embeddings
                                       └── units ── unit_embeddings
          └──────────────────────────────────────────────┘

 運用: suppressions（止める）、operation_log（公開・巻き戻し・昇格などの記録）
```

- **カタログ** は本（`book_id`）ごとに 1 つで、版に属さない（[11](11-catalog-management.md) §1.1）。
- **中身** は「版（原本ごと）× 改訂（個別修正ごと）」の単位で持つ。この単位を `content_id` で表し、中身のテーブルはすべて `content_id` に属する（[10](10-post-release-fixes.md) §3 の後の決定）。
- **止める** は改訂をまたいで効くよう、通し番号ではなく安定キー（`anchor` / `unit_key`）で対象を指す（[09](09-review-strategy.md) §5.1、[10](10-post-release-fixes.md) ①）。

## 2. 命名と型の約束

| 項目 | 約束 |
|---|---|
| ID | 文字列。本は ULID に接頭辞（`bk_01J…`）。中身の ID は親から組み立てる（`bk_01J…:v2:r1`、`…:c00042`） |
| 時刻 | `timestamptz`。運用・表示は日本時間 |
| 期間 | `starts_at` が空 = 即時、`ends_at` が空 = 無期限。仮の日付（`9999-12-31`）は使わない |
| 区分 | `audience`: `free` / `paid`（フラグは `all` も可）。有料は無料を含む |
| 状態 | `text` + `CHECK` 制約で値を限定する |
| 拡張 | `vector`（pgvector）、`btree_gist`（期間の重なり禁止）、`postgis`（位置。旅行・グルメの本を扱うとき） |

## 3. カタログ

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS btree_gist;

-- シリーズ（全集・雑誌）
CREATE TABLE series (
  series_id   text PRIMARY KEY,
  title       text NOT NULL,
  title_kana  text,
  kind        text NOT NULL CHECK (kind IN ('book_series', 'magazine')),
  publisher   text,
  notes       text,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);

-- 本（カタログの中心・書籍レジストリ）
CREATE TABLE books (
  book_id            text PRIMARY KEY,                  -- ULID（初回入稿・予約登録で発行。10 §8）
  title              text NOT NULL,
  subtitle           text,
  title_kana         text,
  original_title     text,
  publisher          text,
  imprint            text,                              -- レーベル
  series_id          text REFERENCES series,
  series_no          text,                              -- 巻・号
  isbn_print         text,                              -- 手入力または原本から（外部取得はしない）
  isbn_ebook         text,
  published_on       date,
  language           text,
  genre              text,
  traits             text[],                            -- 特徴の組み合わせ（04）
  description        text,                              -- 紹介文
  cover_uri          text,
  age_rating         text,
  notes              text,
  catalog_status     text NOT NULL DEFAULT 'draft'
                     CHECK (catalog_status IN ('draft', 'ready', 'published', 'suspended', 'retired')),
  active_content_id  text,                              -- 公開中の中身（book_contents）。FK は下で追加
  field_sources      jsonb NOT NULL DEFAULT '{}',       -- 項目ごとの出所 {"title": "manual", "publisher": "ingest"}（11 §1.2）
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now(),
  updated_by         text
);

-- 寄与者（著者・訳者・監修など）
CREATE TABLE book_contributors (
  book_id    text NOT NULL REFERENCES books ON DELETE CASCADE,
  ord        int  NOT NULL,
  name       text NOT NULL,
  name_kana  text,
  role       text NOT NULL CHECK (role IN ('author', 'translator', 'editor', 'supervisor', 'illustrator', 'other')),
  PRIMARY KEY (book_id, ord)
);

-- 契約上の権利
CREATE TABLE book_rights (
  rights_id     text PRIMARY KEY,
  book_id       text NOT NULL REFERENCES books ON DELETE CASCADE,
  holder        text NOT NULL,
  starts_at     timestamptz,
  ends_at       timestamptz,
  quote_policy  text,                                   -- 本文の引用の可否・量
  contract_ref  text,
  notes         text,
  CHECK (starts_at IS NULL OR ends_at IS NULL OR starts_at < ends_at)
);

-- 公開期間（区分ごとに複数。権利の期間の内側に限る → 保存時に検証）
CREATE TABLE book_publications (
  publication_id text PRIMARY KEY,
  book_id        text NOT NULL REFERENCES books ON DELETE CASCADE,
  audience       text NOT NULL CHECK (audience IN ('free', 'paid')),
  starts_at      timestamptz,
  ends_at        timestamptz,
  reason         text,
  CHECK (starts_at IS NULL OR ends_at IS NULL OR starts_at < ends_at),
  EXCLUDE USING gist (book_id WITH =, audience WITH =,
                      tstzrange(starts_at, ends_at) WITH &&)   -- 同じ区分で期間が重ならない
);

-- キャンペーン用フラグの定義（フロントエンドと共有）
CREATE TABLE flag_definitions (
  flag_key              text PRIMARY KEY,               -- 名前は変えない・再利用しない
  description           text,
  kind                  text NOT NULL CHECK (kind IN ('perpetual', 'limited')),  -- limited は終了日時が必須
  status                text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'deprecated')),
  value_schema          jsonb,                          -- 値の形（JSON Schema）の現在の版
  value_schema_version  int  NOT NULL DEFAULT 1,
  created_at            timestamptz NOT NULL DEFAULT now()
);

-- 本ごとのキャンペーン用フラグ（ON = 今有効な行がある。OFF = ない。11 §2.3.1）
CREATE TABLE book_flags (
  flag_id               text PRIMARY KEY,
  book_id               text NOT NULL REFERENCES books ON DELETE CASCADE,
  flag_key              text NOT NULL REFERENCES flag_definitions,
  value                 jsonb NOT NULL DEFAULT 'true',  -- ON のときの付随情報（並び順・見出し）。false で OFF は表さない
  value_schema_version  int  NOT NULL,
  audience              text NOT NULL DEFAULT 'all' CHECK (audience IN ('all', 'free', 'paid')),
  starts_at             timestamptz,
  ends_at               timestamptz,
  recurrence            text CHECK (recurrence IN ('yearly')),
  recur_from            text,                           -- 'MM-DD'（日本時間）
  recur_to              text,
  created_at            timestamptz NOT NULL DEFAULT now(),
  created_by            text,
  CHECK (starts_at IS NULL OR ends_at IS NULL OR starts_at < ends_at),
  CHECK ((recurrence IS NULL) = (recur_from IS NULL AND recur_to IS NULL))
  -- 期間の重なり（all と free/paid の重なり、毎年の繰り返しを含む）と、limited キーの終了日時必須は保存時に検証
);

-- サンプルプロンプト（季節ものは期間・毎年の繰り返しを持てる）
CREATE TABLE book_sample_prompts (
  prompt_id    text PRIMARY KEY,
  book_id      text NOT NULL REFERENCES books ON DELETE CASCADE,
  text         text NOT NULL,
  intent       text,                                    -- summary | explain | recommend | apply …
  ord          int  NOT NULL DEFAULT 0,
  locale       text NOT NULL DEFAULT 'ja',
  starts_at    timestamptz,
  ends_at      timestamptz,
  recurrence   text CHECK (recurrence IN ('yearly')),
  recur_from   text,
  recur_to     text,
  status       text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'approved', 'retired')),
  source       text NOT NULL CHECK (source IN ('auto', 'manual')),
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now()
);

-- カタログの変更記録
CREATE TABLE catalog_changes (
  change_id  bigserial PRIMARY KEY,
  at         timestamptz NOT NULL DEFAULT now(),
  actor      text NOT NULL,
  entity     text NOT NULL,                             -- books | book_flags | book_publications …
  entity_id  text NOT NULL,
  before     jsonb,
  after      jsonb,
  reason     text
);
```

## 4. 中身（版・改訂）

```sql
-- 版: 原本ごと（改訂版の原本 = 新しい版。10 ④）
CREATE TABLE book_versions (
  book_id          text NOT NULL REFERENCES books ON DELETE CASCADE,
  version          int  NOT NULL,
  source_sha256    text NOT NULL,                       -- 重複入稿の検知に使う
  source_format    text NOT NULL CHECK (source_format IN ('epub', 'pdf')),
  source_filename  text,
  source_uri       text,                                -- gs://…/raw/…
  created_at       timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (book_id, version)
);
CREATE INDEX ON book_versions (source_sha256);

-- 中身の単位: 版 × 改訂（個別修正・再登録のたびに改訂を上げる。10 §3）
CREATE TABLE book_contents (
  content_id      text PRIMARY KEY,                     -- '{book_id}:v{version}:r{revision}'
  book_id         text NOT NULL,
  version         int  NOT NULL,
  revision        int  NOT NULL,
  extractor       text NOT NULL,
  artifacts_uri   text NOT NULL,                        -- gs://…/books/{book_id}/v{version}/{extractor}/
  overrides_hash  text,                                 -- 合成した overrides.json のハッシュ
  status          text NOT NULL DEFAULT 'indexed'
                  CHECK (status IN ('indexed', 'accepted', 'active', 'archived')),
  stats           jsonb,
  qa              jsonb,
  acceptance      jsonb,                                -- 受け入れ判定の結果と根拠（09 §4）
  accepted_at     timestamptz,
  accepted_by     text,
  indexed_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (book_id, version, revision),
  FOREIGN KEY (book_id, version) REFERENCES book_versions ON DELETE CASCADE
);
ALTER TABLE books ADD FOREIGN KEY (active_content_id) REFERENCES book_contents;

CREATE TABLE sections (
  content_id        text NOT NULL REFERENCES book_contents ON DELETE CASCADE,
  section_id        text NOT NULL,
  parent_id         text,
  level             int,
  title             text,
  heading_path      text[],
  ord               int,
  role              text CHECK (role IN ('front', 'body', 'back', 'toc', 'index', 'ad')),
  page_start        int,
  page_end          int,
  page_label_start  text,
  page_label_end    text,
  href              text,
  char_count        int,
  PRIMARY KEY (content_id, section_id)
);

-- 根拠の原文。anchor は内容から作る安定キー（09 §5.1）
CREATE TABLE blocks (
  content_id  text NOT NULL REFERENCES book_contents ON DELETE CASCADE,
  block_id    text NOT NULL,                            -- 通し番号（版の中での並び・会話の引用に使う）
  anchor      text NOT NULL,                            -- hash(search_key(本文)) + 見出しの経路 + 出現番号
  section_id  text NOT NULL,
  ord         int,
  type        text,
  text        text,
  caption     text,
  page_label  text,
  locator     jsonb,
  PRIMARY KEY (content_id, block_id)
);
CREATE INDEX ON blocks (content_id, anchor);

CREATE TABLE chunks (
  chunk_id          text PRIMARY KEY,                   -- '{content_id}:c{ord}'
  content_id        text NOT NULL REFERENCES book_contents ON DELETE CASCADE,
  section_id        text NOT NULL,
  ord               int,
  heading_path      text[],
  role              text,
  block_ids         text[],
  anchors           text[],                             -- 含むブロックの anchor（止める対象との照合用）
  text              text NOT NULL,
  context_header    text,
  page_start        int,
  page_end          int,
  page_label_start  text,
  page_label_end    text,
  href              text,
  token_count       int,
  chunk_strategy    text,
  fts               tsvector                            -- 見出し（重み A）+ 本文（重み B）。和文は文字 bigram
);
CREATE INDEX ON chunks (content_id);
CREATE INDEX ON chunks USING gin (fts);
CREATE INDEX ON chunks USING gin (anchors);

-- 階層要約（節 → 章 → 書籍）
CREATE TABLE summaries (
  summary_id      text PRIMARY KEY,
  content_id      text NOT NULL REFERENCES book_contents ON DELETE CASCADE,
  scope           text NOT NULL CHECK (scope IN ('section', 'chapter', 'part', 'book')),
  section_id      text,                                 -- scope = book のとき NULL
  style           text NOT NULL DEFAULT 'standard',     -- short | standard | detailed
  text            text NOT NULL,
  key_points      jsonb,                                -- 要点と根拠の anchor
  model           text,
  prompt_version  text
);

-- 知識カード（03）。unit_key は改訂をまたいで同じカードを指す安定キー
CREATE TABLE units (
  unit_id         text PRIMARY KEY,                     -- '{content_id}:u{n}'
  content_id      text NOT NULL REFERENCES book_contents ON DELETE CASCADE,
  unit_key        text NOT NULL,                        -- type + 正規化した題名 + 根拠の anchor から作る
  type            text NOT NULL,                        -- principle | recipe | area | spot | shop | menu | character | event | case | method | term …
  schema_version  text NOT NULL,                        -- 例 'recipe/1'
  title           text NOT NULL,
  summary         text,
  attributes      jsonb NOT NULL,                       -- ジャンル別。属性ごとに source = book | inferred | external
  facets          jsonb,                                -- 条件検索用に正規化した値
  tags            text[],
  evidence        jsonb NOT NULL,                       -- [{block_id, anchor, page_label, page}]
  relations       jsonb,                                -- [{type, unit_key}]
  as_of           text,                                 -- 情報の時点（版・号）
  is_ad           boolean NOT NULL DEFAULT false,
  hours           jsonb,                                -- 営業時間（spot / shop）
  geom            geography(Point),                     -- 位置（postgis を入れる場合）
  verification    jsonb,                                -- 機械的な照合・LLM 検証の結果（09 §2）
  override_applied boolean NOT NULL DEFAULT false       -- overrides.json による修正を合成済みか
);
CREATE INDEX ON units (content_id, type);
CREATE INDEX ON units (content_id, unit_key);
CREATE INDEX ON units USING gin (facets jsonb_path_ops);
CREATE INDEX ON units USING gin (tags);

-- 埋め込み（モデルごとに別行。HNSW 索引はモデルごとの部分式索引。05 §2.2）
CREATE TABLE chunk_embeddings (
  chunk_id   text NOT NULL REFERENCES chunks ON DELETE CASCADE,
  model      text NOT NULL,                             -- 例 'vertex-gemini-embedding-001-768'
  dim        int  NOT NULL,
  embedding  vector NOT NULL,
  PRIMARY KEY (chunk_id, model)
);
CREATE TABLE summary_embeddings (
  summary_id text NOT NULL REFERENCES summaries ON DELETE CASCADE,
  model      text NOT NULL, dim int NOT NULL, embedding vector NOT NULL,
  PRIMARY KEY (summary_id, model)
);
CREATE TABLE unit_embeddings (
  unit_id    text NOT NULL REFERENCES units ON DELETE CASCADE,
  model      text NOT NULL, dim int NOT NULL, embedding vector NOT NULL,
  PRIMARY KEY (unit_id, model)
);
-- 例: CREATE INDEX … ON chunk_embeddings USING hnsw ((embedding::vector(768)) vector_cosine_ops)
--       WHERE model = 'vertex-gemini-embedding-001-768';
```

## 5. 運用

```sql
-- 止める（10 ①）。正本は overrides.json、こちらは DB 側の写し。安定キーで指すので改訂をまたいで効く
CREATE TABLE suppressions (
  suppression_id  text PRIMARY KEY,
  book_id         text NOT NULL REFERENCES books,
  target_kind     text NOT NULL CHECK (target_kind IN ('book', 'block', 'unit')),
  target_key      text,                                 -- block: anchor / unit: unit_key / book: NULL
  reason          text NOT NULL,
  actor           text NOT NULL,
  created_at      timestamptz NOT NULL DEFAULT now(),
  lifted_at       timestamptz,                          -- 解除（行は消さない）
  overrides_ref   text                                  -- 対応する overrides.json の記録
);
CREATE INDEX ON suppressions (book_id) WHERE lifted_at IS NULL;

-- 公開・巻き戻し・受け入れ・昇格・止めるの操作記録（10 §6.4）
CREATE TABLE operation_log (
  op_id       bigserial PRIMARY KEY,
  at          timestamptz NOT NULL DEFAULT now(),
  actor       text NOT NULL,
  op          text NOT NULL,                            -- index | accept | publish | rollback | promote | suppress | lift | reconcile
  book_id     text,
  content_id  text,
  detail      jsonb,
  result      text NOT NULL                             -- ok | partial | failed
);
```

### 5.1 公開の判定と、フロントエンドに返すもの

```sql
-- 区分・時刻ごとに、見せてよい本（11 §1.3）
--   公開期間（free は free の期間、paid は free ∪ paid の期間）の中
--   ∧ 権利の期間の中 ∧ active_content_id があり、その状態が active（受け入れ判定済み）
--   ∧ 本単位で止められていない ∧ catalog_status = 'published'
CREATE FUNCTION visible_books(p_audience text, p_at timestamptz)
  RETURNS TABLE (book_id text, content_id text) … ;

-- 本ごとに、今 ON のフラグ（区分・時刻で評価。毎年の繰り返しも展開）
CREATE FUNCTION active_flags(p_audience text, p_at timestamptz)
  RETURNS TABLE (book_id text, flag_key text, value jsonb) … ;

-- 本ごとに、今出してよいサンプルプロンプト（承認済み・期間内）
CREATE FUNCTION active_sample_prompts(p_at timestamptz)
  RETURNS TABLE (book_id text, prompt_id text, text text, intent text, ord int) … ;
```

検索（チャンク・カード・要約）は、`visible_books` の `content_id` に絞り、止められた `anchor`（チャンクは `anchors` が重なるもの）・`unit_key` を除いて行う。

## 6. 現在の実装からの移行

| 現在（`schema.sql`） | 本書 | 変更 |
|---|---|---|
| `books`（書誌の一部と `active_version`） | `books` | カタログの項目を追加。`active_version` → `active_content_id`。`book_id` をハッシュ由来から ULID に |
| `book_versions`（状態・統計を持つ） | `book_versions` + `book_contents` | 改訂を分離し、状態・統計・受け入れ判定は `book_contents` へ |
| `sections` / `blocks` / `chunks`（`(book_id, version)` に属する） | 同名 | `content_id` に属する形に。`blocks.anchor`・`chunks.anchors` を追加 |
| `chunk_embeddings` | 同名 | 変更なし |
| — | `series`、`book_contributors`、`book_rights`、`book_publications`、`flag_definitions`、`book_flags`、`book_sample_prompts`、`catalog_changes` | 新規（カタログ） |
| — | `summaries`、`units`、`summary_embeddings`、`unit_embeddings` | 新規（要約・知識カード） |
| — | `suppressions`、`operation_log` | 新規（運用） |

現状は本番データがないため、移行スクリプトは作らず定義を作り直す。

## 7. 本書で決めたこと（[00](00-decisions.md) へ反映）

- 中身の単位は `content_id`（版 × 改訂）。中身のテーブルはすべてこれに属する。
- 止める対象は通し番号ではなく安定キー（`anchor` / `unit_key`）で指し、`suppressions` を検索時に除外条件として使う。これで止めた状態が改訂・版をまたいで効き、再登録の必要がない。
- サンプルプロンプトも、フラグと同じく期間・毎年の繰り返しを持てる。
- 埋め込みは対象ごと（チャンク・要約・カード）に別テーブル。どれもモデルごとに別行。
- 会話（利用者・会話・メッセージ・引用）のテーブルはアプリ側の設計とし、本書の範囲外。引用は `(content_id, block_id)` で記録し、本が見えなくなったら会話も見せない（[11](11-catalog-management.md) §6）。

## 8. 未決事項

1. **位置（`geom`）のために PostGIS を入れる時期。** 旅行・グルメの本を扱うまでは列を作らなくてよい。
2. **昇格で埋め込みを運ぶか。** 運ぶなら成果物に埋め込みを含める（[06](06-deployment-gcp.md) §2.3）。
3. **古い改訂・版の削除の時期。** 巻き戻し用に数週間は残す（[10](10-post-release-fixes.md) §10）。会話は本が見えなくなったら見せないため、会話のために古い改訂を長く残す必要はない。
