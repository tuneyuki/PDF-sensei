-- PDF-sensei Index 層（[6] Index）。設計: docs/design/01, 03
-- 何度流しても同じ結果になるように IF NOT EXISTS で書く。

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS books (
  book_id        text PRIMARY KEY,
  title          text,
  authors        text[],
  isbn           text,
  language       text,
  active_version int,                 -- 検索対象のバージョン（公開中）
  created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS book_versions (
  book_id         text NOT NULL REFERENCES books ON DELETE CASCADE,
  version         int  NOT NULL,
  source_sha256   text NOT NULL,
  source_format   text NOT NULL,
  source_filename text,
  extractor       text,
  canonical_uri   text,               -- doc.json の場所
  status          text NOT NULL,      -- indexed | active | archived
  stats           jsonb,
  qa              jsonb,
  indexed_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (book_id, version)
);

CREATE TABLE IF NOT EXISTS sections (
  book_id          text NOT NULL,
  version          int  NOT NULL,
  section_id       text NOT NULL,
  parent_id        text,
  level            int,
  title            text,
  heading_path     text[],
  ord              int,
  role             text,
  page_start       int,
  page_end         int,
  page_label_start text,
  page_label_end   text,
  href             text,
  char_count       int,
  PRIMARY KEY (book_id, version, section_id),
  FOREIGN KEY (book_id, version) REFERENCES book_versions ON DELETE CASCADE
);

-- 根拠の原文（引用の表示・検証に使う）
CREATE TABLE IF NOT EXISTS blocks (
  book_id    text NOT NULL,
  version    int  NOT NULL,
  block_id   text NOT NULL,
  section_id text NOT NULL,
  ord        int,
  type       text,
  text       text,
  caption    text,
  page_label text,
  locator    jsonb,
  PRIMARY KEY (book_id, version, block_id),
  FOREIGN KEY (book_id, version) REFERENCES book_versions ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS chunks (
  chunk_id         text PRIMARY KEY,
  book_id          text NOT NULL,
  version          int  NOT NULL,
  section_id       text NOT NULL,
  ord              int,
  heading_path     text[],
  role             text,
  block_ids        text[],
  text             text NOT NULL,
  context_header   text,
  page_start       int,
  page_end         int,
  page_label_start text,
  page_label_end   text,
  href             text,
  token_count      int,
  chunk_strategy   text,
  fts              tsvector,          -- 日本語は文字 bigram、英語は単語（sensei.fts）
  FOREIGN KEY (book_id, version) REFERENCES book_versions ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS chunks_book_idx ON chunks (book_id, version);
CREATE INDEX IF NOT EXISTS chunks_fts_idx ON chunks USING gin (fts);

-- 埋め込みはモデルごとに別行で持つ（モデルの比較・切り替えのため）。
-- 次元はモデルで異なるので列は次元なしの vector とし、HNSW 索引はモデルごとの部分式索引で作る
-- （sensei.db.store.ensure_vector_index）。
CREATE TABLE IF NOT EXISTS chunk_embeddings (
  chunk_id  text NOT NULL REFERENCES chunks ON DELETE CASCADE,
  model     text NOT NULL,
  dim       int  NOT NULL,
  embedding vector NOT NULL,
  PRIMARY KEY (chunk_id, model)
);
