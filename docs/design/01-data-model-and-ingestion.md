# 01. データ格納形式と入稿パイプライン設計（ドラフト）

EPUB / PDF の書籍を元に「その本の先生」として振る舞うチャットボットのための、データ格納形式と入稿（ingestion）パイプラインの設計案。

## 1. 要件から導く設計方針

チャットボットに求める振る舞いと、それに必要なデータの粒度を対応させる。

| ユースケース | 例 | 必要なデータ |
|---|---|---|
| 要約 | 「3章を要約して」「この本の主張は？」 | 章・節・書籍単位の**階層的要約**、章本文の一括取得 |
| 解説 | 「p.45 の○○ってどういう意味？」 | 原文の**正確な段落**と前後文脈、**出典位置**（章/節/ページ） |
| ナレッジ活用の分析・提案 | 「この本の考え方でうちの課題を分析して」 | 本が提示する**概念・フレームワーク・主張**の構造化データ、複数書籍横断検索 |

ここから以下の方針を置く。

1. **3層構造で保存する**: 原本（Raw） → 正規化ドキュメント（Canonical） → 検索・知識インデックス（Index）。Canonical を唯一の正（source of truth）とし、チャンク分割や埋め込みはいつでも作り直せるようにする。
2. **EPUB と PDF を同じ中間表現に揃える**: 後段（チャンク化・要約・検索）はフォーマットを意識しない。
3. **すべてのテキストに出典位置（locator）を保持する**: 回答で「第3章 2節（p.45）」と引用でき、解説時に原文へ戻れる。
4. **階層（書籍 > 章 > 節 > ブロック）を保持する**: 要約は階層的に、検索は細粒度で、回答時は親の文脈を読み込む（small-to-big）。
5. **パイプラインは段階ごとに成果物を保存し、冪等・再実行可能にする**: 抽出器やモデルを差し替えたとき、影響する段以降だけ再処理する。

## 2. データの3層構造

```
┌─────────────────────────────────────────────────────────────┐
│ Layer 0: Raw          原本ファイル（不変・ハッシュで管理）          │
│   s3://bucket/raw/{sha256}.epub|pdf                          │
├─────────────────────────────────────────────────────────────┤
│ Layer 1: Canonical    正規化ドキュメント（DocTree JSON + Markdown）  │
│   s3://bucket/canonical/{book_id}/{version}/doc.json         │
│   s3://bucket/canonical/{book_id}/{version}/doc.md           │
│   s3://bucket/canonical/{book_id}/{version}/assets/fig-*.png │
├─────────────────────────────────────────────────────────────┤
│ Layer 2: Index        検索・知識（PostgreSQL + pgvector + 全文検索） │
│   sections / blocks / chunks(+embedding) / summaries /       │
│   concepts / concept_mentions                                │
└─────────────────────────────────────────────────────────────┘
```

### 2.1 Layer 0: Raw

- 原本はファイル内容の SHA-256 をキーに保存（重複入稿の検知、改訂版の識別）。
- 書き換えない。再処理は常に Raw から再現できること。
- DRM 付き EPUB/PDF は対象外（入稿時に検知してエラー）。

### 2.2 Layer 1: Canonical（DocTree）

EPUB・PDF 共通の中間表現。JSON を正とし、人間のレビュー・LLM への一括投入用に Markdown も併せて出力する。

```jsonc
{
  "schema_version": "1.0",
  "book": {
    "book_id": "bk_01J...",            // ULID
    "version": 1,                      // 同一書籍の再入稿・改訂で増える
    "source_sha256": "ab12...",
    "source_format": "epub",           // "epub" | "pdf"
    "title": "○○入門",
    "authors": ["山田 太郎"],
    "publisher": "...",
    "isbn": "978-...",
    "language": "ja",
    "published_at": "2024-03-01",
    "extraction": {                    // 再現性のための記録
      "extractor": "epub-xhtml",       // or "docling", "pymupdf", "ocr:..."
      "extractor_version": "0.3.0",
      "ocr_used": false
    }
  },
  "toc": [ /* 目次（sections を参照する木構造）*/ ],
  "sections": [
    {
      "section_id": "sec_0003",
      "parent_id": "sec_0001",
      "level": 2,                      // 1=部/章, 2=節, 3=項 ...
      "title": "2. 需要予測の基本",
      "heading_path": ["第3章 需要予測", "2. 需要予測の基本"],
      "order": 12,
      "locator": { "page_start": 44, "page_end": 51 }   // PDF の場合
    }
  ],
  "blocks": [
    {
      "block_id": "blk_000412",
      "section_id": "sec_0003",
      "order": 412,
      "type": "paragraph",             // heading|paragraph|list|table|figure|code|quote|footnote|equation|caption
      "text": "需要予測とは……",
      "html": null,                    // 表などは構造を保持（table は markdown/html）
      "locator": {
        // PDF:  page と bbox（複数ページにまたがる段落は配列）
        "pdf": [{ "page": 45, "bbox": [72.0, 120.5, 523.2, 300.1] }],
        // EPUB: spine 上のファイルと要素位置（CFI）
        "epub": { "href": "OEBPS/ch03.xhtml", "cfi": "/6/14!/4/2/8", "anchor": "p-412" }
      },
      "page_label": "45",              // 書籍に印字されたページ番号（PDF の物理ページと異なる場合あり）
      "refs": { "footnotes": ["blk_000980"], "figures": [] },
      "quality": { "ocr_confidence": null, "flags": [] }
    }
  ],
  "assets": [
    { "asset_id": "fig-07", "path": "assets/fig-07.png", "caption_block_id": "blk_000415",
      "description": "（LLM 生成の図解説、任意）" }
  ]
}
```

ポイント:

- **ブロック = 意味のある最小単位**（段落・表・図など）。チャンクはブロックを束ねて作るので、チャンク戦略を変えても Canonical は不変。
- **ID は安定させる**（`order` ベース + version）。会話履歴やブックマークから参照可能にする。
- 日本語特有の扱い:
  - ルビ（`<ruby>漢字<rt>かんじ</rt></ruby>`）は本文は親文字のみ、ルビは `annotations` に保持（検索ノイズ防止）。
  - 保存は Unicode NFC。検索キー生成時のみ NFKC（全角英数→半角など）を使う。
  - PDF の行末改行は日本語ではスペースを入れずに連結、英語はハイフネーション解除してスペース連結。

### 2.3 Layer 2: Index（PostgreSQL）

まずは **PostgreSQL 1台に集約**（pgvector でベクトル検索、PGroonga または pg_bigm で日本語全文検索）。規模が大きくなったら検索部分を OpenSearch 等に切り出す。

```sql
-- 書籍とバージョン
CREATE TABLE books (
  book_id        text PRIMARY KEY,
  title          text NOT NULL,
  authors        text[],
  isbn           text,
  language       text,
  active_version int,                -- 公開中のバージョン
  created_at     timestamptz DEFAULT now()
);

CREATE TABLE book_versions (
  book_id        text REFERENCES books,
  version        int,
  source_sha256  text NOT NULL,
  source_format  text NOT NULL,      -- epub | pdf
  canonical_uri  text NOT NULL,      -- s3://.../doc.json
  status         text NOT NULL,      -- ingesting | review | active | archived | failed
  stats          jsonb,              -- ページ数, 文字数, OCR率, 警告件数など
  PRIMARY KEY (book_id, version)
);

-- 章・節（階層）
CREATE TABLE sections (
  section_id   text,
  book_id      text,
  version      int,
  parent_id    text,
  level        int,
  title        text,
  heading_path text[],
  ord          int,
  page_start   int,
  page_end     int,
  full_text    text,                 -- 章/節の全文（要約・一括読込用）
  token_count  int,
  PRIMARY KEY (book_id, version, section_id)
);

-- 検索単位
CREATE TABLE chunks (
  chunk_id       text PRIMARY KEY,
  book_id        text,
  version        int,
  section_id     text,
  block_ids      text[],             -- 元ブロック（引用・ハイライト用）
  ord            int,
  text           text NOT NULL,      -- 本文
  context_header text,               -- 「書名 > 3章 > 2節」+ LLM 生成の文脈説明
  page_start     int,
  page_end       int,
  token_count    int,
  chunk_strategy text,               -- 例 "struct-v1-600tok"
  embedding      vector(1024),
  embedding_model text,              -- モデル名+版。モデル変更時の再埋め込み判定に使用
  search_text    text                -- NFKC 正規化済み全文検索用
);
CREATE INDEX ON chunks USING hnsw (embedding vector_cosine_ops);
-- CREATE INDEX ON chunks USING pgroonga (search_text);

-- 階層要約（節 → 章 → 書籍）
CREATE TABLE summaries (
  summary_id   text PRIMARY KEY,
  book_id      text,
  version      int,
  scope        text,                 -- section | chapter | book
  section_id   text,                 -- scope=book のとき NULL
  style        text,                 -- short(3行) | standard | detailed
  text         text,
  key_points   jsonb,                -- 箇条書きの要点（出典 block_id 付き）
  embedding    vector(1024),
  model        text,
  prompt_version text
);

-- ナレッジ（概念・フレームワーク・主張）
CREATE TABLE concepts (
  concept_id   text PRIMARY KEY,
  book_id      text,
  version      int,
  kind         text,                 -- term | framework | principle | claim | method | case_study
  name         text,
  aliases      text[],
  definition   text,                 -- 本書における定義・説明
  details      jsonb,                -- framework の構成要素・手順・前提条件・適用場面など
  embedding    vector(1024)
);

CREATE TABLE concept_mentions (
  concept_id   text REFERENCES concepts,
  chunk_id     text REFERENCES chunks,
  role         text,                 -- definition | example | application | critique
  PRIMARY KEY (concept_id, chunk_id)
);

CREATE TABLE concept_relations (     -- 任意: 簡易ナレッジグラフ
  src_concept  text,
  dst_concept  text,
  relation     text,                 -- part_of | causes | contrasts_with | prerequisite_of ...
  evidence_chunk_id text
);
```

#### チャット時にどう使うか（設計の検証）

| 質問タイプ | 取得の仕方 |
|---|---|
| 「3章を要約して」 | `summaries(scope=chapter)` を返す／詳細指示なら `sections.full_text` を丸ごと LLM に渡す |
| 「この本の要点は？」 | `summaries(scope=book)` + 各章の `key_points` |
| 「○○ってどういう意味？」 | `concepts` の定義 + ハイブリッド検索（ベクトル + 全文）で chunk → 親 section へ拡張して回答、出典を引用 |
| 「この本の考え方でうちの課題を分析して」 | `concepts(kind=framework/principle)` を検索 → 構成要素・適用条件に沿って分析、根拠 chunk を引用 |
| 複数書籍横断 | 同じテーブルを `book_id` 絞り込みなしで検索 |

> 補足: 1冊（日本語 300 ページ前後で 15〜25 万トークン程度）なら、プロンプトキャッシュを使って書籍全文をコンテキストに載せる方式も現実的。
> そのため本設計では **章単位の全文（`sections.full_text`）を常に取り出せる**ようにし、「RAG による検索」と「章・書籍の全文投入」をクエリに応じて使い分けられるようにしている。

## 3. 入稿パイプライン

### 3.1 全体像

```
 upload
   │
   ▼
[0] Intake ──► [1] Extract ──► [2] Normalize ──► [3] Structure ──► Canonical(doc.json/md)
                 │  EPUB / PDF(テキスト) / PDF(スキャン→OCR)                    │
                                                                                ▼
[8] Publish ◄── [7] QA ◄── [6] Index ◄── [5] Enrich ◄── [4] Chunk ◄────────────┘
                            (embed)     (要約/概念抽出/文脈付与)
```

各段は `(source_sha256, stage, stage_version, config_hash)` をキーに成果物を保存し、同じ入力なら再実行をスキップする。段のバージョンを上げた場合は、その段以降のみ再処理する。

### 3.2 各段の詳細

#### [0] Intake（受付）
- SHA-256 計算 → 既存なら重複として停止（またはバージョン追加）。
- 形式判定（マジックバイト）、DRM/暗号化検知、ファイルサイズ・ページ数の上限チェック。
- メタデータ取得: EPUB は OPF（`dc:title`, `dc:creator`, `dc:identifier` 等）、PDF は Info/XMP。不足分は入稿者が補完できる UI/CLI 引数を用意。
- `book_versions.status = ingesting`。

#### [1] Extract（抽出）

**EPUB**
- ZIP 展開 → `META-INF/container.xml` → OPF → spine の順に XHTML を読む。
- 目次は EPUB3 の `nav.xhtml`、なければ EPUB2 の `toc.ncx`。
- XHTML を DOM 走査して `h1-h6 / p / ul / ol / table / figure / blockquote / pre / aside(epub:type=footnote)` をブロック化。CSS クラスで見出しを表現している出版社も多いため、クラス名→ブロック種別のマッピングを設定で持てるようにする。
- 画像は assets に保存、`alt` とキャプションを紐づけ。
- locator として spine href + 要素 ID / CFI を付与。ページ番号は `epub:type="pagebreak"` があれば `page_label` に反映。
- ライブラリ候補: `ebooklib` + `lxml`/`selectolax`。

**PDF**
- まずページごとに **テキスト層の有無を判定**（抽出文字数・文字化け率）。
  - テキスト PDF: レイアウト解析で段組・読み順・見出し・表・図を検出。
  - スキャン PDF / テキスト層が壊れているページ: OCR。
- 目次は PDF のアウトライン（しおり）を最優先、なければフォントサイズ・太字・番号パターン（「第N章」「1.2」）から見出しを推定。
- ヘッダ・フッタ・柱・ノンブルは「複数ページで同位置に繰り返し出る短いテキスト」として除去。
- ライブラリ候補（比較検証して選定する）:
  - **Docling**（レイアウト解析・表・読み順・OCR 統合、構造化出力）← 第一候補
  - Marker / MinerU（Markdown 化の品質が高い）
  - PyMuPDF（高速・bbox 取得、ベースライン/フォールバック）
  - OCR: Docling 内蔵 OCR、または LLM ビジョン（ページ画像を渡して構造化テキスト化）。縦書き・図表の多い和書は LLM ビジョンが有利な場合が多い。

#### [2] Normalize（正規化）
- 改行連結（日本語は詰めて連結、英語はハイフネーション解除）。
- ページをまたぐ段落の結合（前ページ末が句点で終わらない等のヒューリスティック）。
- 脚注の本文参照への紐付け、図表キャプションとの紐付け。
- Unicode NFC、制御文字除去、合字（ﬁ 等）展開。
- ルビの分離。

#### [3] Structure（構造化）
- 目次と見出しブロックから section 木を構築し、全ブロックに `section_id` を付与。
- 安定 ID 採番、`heading_path` 生成。
- → **doc.json / doc.md / assets を Canonical として保存**（ここまでが「書籍の正」）。

#### [4] Chunk（チャンク分割）
- **構造準拠**: 節の境界をまたがない。表・コード・図キャプションは分割しない。
- 目安 400〜800 トークン（日本語でおおよそ 600〜1,200 文字）、オーバーラップは 1 ブロック程度。
- 小さすぎる節は隣接ブロックと結合、長すぎる段落は文境界で分割。
- 各チャンクに `heading_path` を付与（検索精度と引用表示のため）。
- 戦略は `chunk_strategy` として記録し、A/B 比較できるようにする。

#### [5] Enrich（LLM による付加情報）
- **文脈ヘッダ付与（Contextual Retrieval）**: 章全文 + チャンクを LLM に渡し「このチャンクが本全体のどの話の一部か」を 1〜2 文で生成し `context_header` に保存。埋め込み・全文検索の対象に含める。章全文をプロンプトキャッシュに載せればコストは抑えられる。
- **階層要約**: 節 → 章 → 書籍 のボトムアップで生成。要点には根拠 `block_id` を付ける（要約の出典トレース用）。
- **ナレッジ抽出**: 用語・フレームワーク・原則・主張・手法・事例を構造化抽出（JSON スキーマで出力させる）。書籍内で同一概念を名寄せ。
- **図表の説明文**（任意）: 図をビジョンモデルで説明文化し、検索対象にする。
- プロンプトとモデルは `prompt_version` / `model` として記録。

#### [6] Index（埋め込み・登録）
- chunks / summaries / concepts を埋め込み、PostgreSQL に登録。
- 埋め込みモデルは日本語性能を重視して選定（多言語対応の商用 API、または日本語特化 OSS モデル）。候補を実データで評価してから決定。
- 全文検索用の `search_text`（NFKC + 必要なら読み仮名）を作成。
- 新バージョンは `version` を分けて書き込み、公開切替までは検索対象にしない。

#### [7] QA（品質チェック）
自動チェックし、閾値を下回れば `status = review` で人手確認に回す。
- 抽出文字数 / ページ数（極端に少ないページ = 抽出失敗や画像ページ）
- OCR 信頼度、文字化け率（制御文字・置換文字 U+FFFD の割合）
- 目次の章数と section 数の一致、見出しが取れていない長大セクション
- 全ブロックがいずれかのチャンクに含まれているか（カバレッジ）
- サンプル質問による検索スモークテスト（例: 各章タイトルで検索してその章がヒットするか）

#### [8] Publish（公開）
- `books.active_version` を新バージョンに切り替え（アトミック）。旧バージョンは `archived`。

### 3.3 実行基盤

- 入稿はバッチ的で LLM 呼び出しが長時間になりうるため、**ジョブキュー + ワーカー**構成にする。
  - MVP: Python CLI（`sensei ingest path/to/book.pdf`）で同期実行。
  - 本番: アップロード → キュー（SQS / Cloud Tasks / Redis 等）→ ワーカーで段ごとに実行、`ingestion_jobs` テーブルで進捗管理・失敗段から再開。
- LLM 呼び出しはバッチ API を使うとコストを下げられる（要約・概念抽出は即時性不要）。

```sql
CREATE TABLE ingestion_jobs (
  job_id        text PRIMARY KEY,
  book_id       text,
  version       int,
  stage         text,               -- intake|extract|normalize|structure|chunk|enrich|index|qa|publish
  stage_version text,
  status        text,               -- pending|running|succeeded|failed|skipped
  input_hash    text,
  output_uri    text,
  error         text,
  started_at    timestamptz,
  finished_at   timestamptz
);
```

## 4. 段階的な進め方（提案）

| フェーズ | 内容 |
|---|---|
| **MVP** | EPUB + テキスト PDF のみ。Extract→Structure→Chunk→Embed、PostgreSQL+pgvector、章/書籍要約。チャットは「検索 + 章全文投入」で回答・引用。 |
| Phase 2 | スキャン PDF の OCR、Contextual Retrieval、ハイブリッド検索（全文+ベクトル）、QA 自動チェック、評価用質問セット。 |
| Phase 3 | ナレッジ抽出（概念・フレームワーク）、分析・提案モード、複数書籍横断、図表の説明文化、管理 UI。 |

## 5. 未決事項（要確認）

1. **想定規模**: 冊数（数冊 / 数百冊 / それ以上）と 1 冊あたりのページ数。→ 単一 Postgres で足りるか、全文投入方式をどこまで使えるかが変わる。
2. **書籍の性質**: 和書中心か洋書も混在か。縦書き・スキャン PDF・図表の多い技術書の割合。→ PDF 抽出器と OCR 方式の選定に影響。
3. **利用形態と権利**: 個人利用か社内共有か。社内配布の場合は書籍ごとのアクセス制御（`book_acl`）が必要。著作権・ライセンス上の扱いも確認。
4. **実行環境**: クラウド（AWS / GCP / Azure）の指定、外部 LLM API にテキストを送ってよいか（オンプレ/閉域要件の有無）。
5. **入稿者**: 開発者が CLI で入れるのか、利用者が Web から自由にアップロードするのか。→ QA と人手レビューの重み付けが変わる。
