# 05. チャンク分割・登録・検索の実装と評価

[04](04-architecture-traits.md) の進め方 1（共通の土台）のうち、**[4] Chunk → [6] Index → 検索 → 検索の評価** を実装した。DB はローカルの Docker（PostgreSQL 16 + pgvector）で動かす。

## 1. 手元での動かし方

```bash
# 1. DB を起動
docker compose -f docker/compose.yml up -d
export SENSEI_DSN=postgresql://sensei:sensei@localhost:5432/sensei

# 2. インストール（ローカル埋め込みを使う場合は st を付ける）
uv venv -p 3.12 .venv && uv pip install -p .venv/bin/python -e '.[dev,st]'
.venv/bin/sensei db init

# 3. 入稿 → 登録（初回はモデルを Hugging Face から取得する）
.venv/bin/sensei ingest path/to/book.epub --out out
.venv/bin/sensei index out/bk_xxxx/v1/epub --embedder st:cl-nagoya/ruri-v3-310m

# 4. 検索・評価
.venv/bin/sensei search "間違って git add したファイルを戻したい" --embedder st:cl-nagoya/ruri-v3-310m
.venv/bin/sensei eval eval/progit-ja.yaml --book bk_xxxx --mode hybrid --embedder st:cl-nagoya/ruri-v3-310m -v

# 別のモデルの埋め込みを後から追加して比較する
.venv/bin/sensei embed --book bk_xxxx --embedder voyage:voyage-3.5
```

DB の結合テストは `SENSEI_TEST_DSN` を設定したときだけ動く（`tests/test_db.py`）。

## 2. 実装の要点

### 2.1 [4] Chunk（`src/sensei/chunk.py`）

- 節の境界をまたがない。同じ節に直接属するブロックだけを束ねる。
- 目安 500 トークン、上限 800、120 未満の末尾は直前に併合する。トークン数は概算（和文 1 字 ≒ 1、それ以外 4 字 ≒ 1）。
- 表・コード・箇条書きは途中で切らない。上限を超えるものだけ行単位（本文は文単位）で分割する。
- 目次・索引（`role=toc/index`）は対象外。
- 各チャンクに見出しの経路・ページ範囲（印字ページと PDF 上のページ）・元ブロック ID を持たせる。
- `context_header` は現状「書名 > 章 > 節」。LLM による文脈説明（Contextual Retrieval）は Enrich 段で追記する。
- 入稿パイプラインの段として組み込み、`chunks.json` に出力する（キャッシュ対象）。QA に「本文ブロックが全てどれかのチャンクに入っているか」を追加した。

| | ja EPUB | ja PDF | en EPUB | en PDF |
|---|---:|---:|---:|---:|
| チャンク数 | 802 | 807 | 676 | 674 |
| トークン数の中央値 | 479 | 477 | 276 | 281 |

英語版の中央値が小さいのは、節が短いため（節の境界を守っている）。

### 2.2 [6] Index（`src/sensei/db/schema.sql`, `store.py`）

- テーブル: `books`, `book_versions`, `sections`, `blocks`（根拠の原文）, `chunks`, `chunk_embeddings`。
- 同じ (book_id, version) の再登録は置き換え（トランザクション内で削除 → 挿入）。登録時に公開中バージョン（`books.active_version`）を切り替える。検索は公開中バージョンだけが対象。
- **埋め込みはモデルごとに別行で持つ。** 次元はモデルで違うため列は次元なしの `vector` とし、HNSW 索引はモデルごとの部分式索引（`embedding::vector(dim)` かつ `model = ...`）で作る。複数モデルを並べて比較・切り替えできる。

### 2.3 全文検索（`src/sensei/fts.py`）

- PostgreSQL 標準の全文検索で日本語を扱うため、**和文は文字 bigram、英数字は単語** に分けてから `to_tsvector('simple', …)` に渡す。拡張（PGroonga / pg_bigm）なしで動く。規模が大きくなったら PGroonga への切り替えを検討する。
- 見出し（節・項）の語は重み A、本文は重み B。
- 検索語からひらがなだけの bigram（「した」「ます」）を除く。
- 順位付けは `ts_rank`（評価で `ts_rank_cd` より明確に良かった。§3）。

### 2.4 埋め込み（`src/sensei/embed.py`）

`--embedder 提供元:モデル名` で差し替える。

| 指定 | 内容 |
|---|---|
| `st:cl-nagoya/ruri-v3-310m` | ローカル実行（API キー不要）。日本語向けの推奨。検索文・文書の接頭辞（`検索クエリ: ` / `検索文書: `）は自動で付ける |
| `st:intfloat/multilingual-e5-large` | ローカル実行。多言語 |
| `voyage:voyage-3.5` | Voyage AI API（`VOYAGE_API_KEY`） |
| `hash` | 文字 n-gram のハッシュ。意味は扱えない。配線確認・テスト専用 |

チャンクの埋め込み対象は「見出しの経路 + 本文」。

### 2.5 検索（`src/sensei/search.py`）

全文検索とベクトル検索の上位 50 件ずつを **RRF（Reciprocal Rank Fusion）** で統合する。結果には出典表示用の文字列（`『Pro Git』Git の基本 > 作業のやり直し > ステージしたファイルの取り消し（p.47）`）を付ける。

## 3. 評価

### 3.1 評価セット

`eval/progit-ja.yaml`: Pro Git 日本語版に対する 40 問。質問はユーザーの言い方で書き（見出しの言葉をなるべく避ける）、正解は見出しで示す。指標は hit@k（上位 k 件に正解の節が入った割合）と MRR。

### 3.2 結果（この環境で測定できた範囲）

この開発環境ではネットワーク方針で Hugging Face と埋め込み API が遮断されており、**意味を扱える埋め込みモデルでの評価はできていない**。以下は全文検索のみの結果。

| 検索方式 | 対象 | hit@1 | hit@5 | hit@10 | MRR |
|---|---|---:|---:|---:|---:|
| 全文検索（`ts_rank_cd`、初版） | ja EPUB | 27.5% | 52.5% | 67.5% | 0.385 |
| 全文検索（`ts_rank`） | ja EPUB | **47.5%** | **60.0%** | **75.0%** | **0.539** |
| 全文検索（`ts_rank`） | ja PDF | 45.0% | 60.0% | 75.0% | 0.522 |

分かったこと:

- **EPUB と PDF で検索性能はほぼ同じ。** 抽出の差（[02](02-ingestion-mvp-validation.md)）は検索品質のボトルネックになっていない。
- 全文検索で取りこぼすのは、**言い換え** の質問。例: 「作業途中の変更を退避」→ stash、「暗号的に証明」→ 署名、「よく使うコマンドを短く打つ」→ エイリアス、「Git に移行」→ Subversion からの移行。ここは意味を扱える埋め込みモデルで補う必要がある。
- `hash` 埋め込みを混ぜたハイブリッドは全文検索単独より悪化した（意味を持たないベクトルが雑音になる）。`hash` は配線確認専用。

### 3.3 手元で測ってほしいこと

```bash
for m in fts vector hybrid; do
  sensei eval eval/progit-ja.yaml --book <ja の book_id> --mode $m --embedder st:cl-nagoya/ruri-v3-310m
done
```

期待値の目安: 言い換えの質問が拾えるようになり、hybrid の hit@5 が全文検索単独（60%）を大きく上回ること。上回らない場合は、チャンクの大きさ・見出しの付け方・RRF の重みを調整する。

## 4. 次のステップ

本番は GCP の Cloud Run で動かし、ベクトル検索を併用する（[06](06-deployment-gcp.md)）。検索精度の評価はいったん止め、本番構成ができたら同じ評価セットで測る。

1. 意味検索の評価（本番の埋め込みモデルで実施）と、英語版の評価セット。
2. [5] Enrich: 文脈ヘッダ（Contextual Retrieval）と階層要約。LLM API が必要。
3. 根拠の検証（引用文が `blocks` に実在するか、ページ番号が一致するか）と、先生の道具（[03](03-knowledge-units-and-teacher.md) §4.1）の最初の版。
4. 書籍レジストリ: 同じ作品の EPUB と PDF、改訂版を 1 つの book_id にまとめる（現状はファイルのハッシュ単位で別の book_id）。
