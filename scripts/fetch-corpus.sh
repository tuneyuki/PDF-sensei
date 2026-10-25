#!/usr/bin/env bash
# 題材となる書籍（EPUB / PDF）を data/corpus/ に集める。
#
#   scripts/fetch-corpus.sh            # core のみ（Pro Git 4 ファイル。compare 用の二形式そろい）
#   scripts/fetch-corpus.sh core ja    # 層を選ぶ
#   scripts/fetch-corpus.sh all        # 全部（約 150MB）
#   scripts/fetch-corpus.sh --list     # 取得せず一覧だけ表示
#
# 層:
#   core  同一内容の EPUB と PDF がそろう。`sensei compare` で抽出品質を測れる唯一の層
#   ja    日本語の PDF。2 段組・図表の多い官公庁文書。和文抽出と FTS の難所
#   en    英語の書籍規模 PDF。数式・コード・索引つき
#   epub  構造のきれいな EPUB。EPUB 抽出の基準として
#
# 書籍ファイルはリポジトリに入れない（.gitignore で data/ を除外）。
# 各ファイルのライセンスは下表の LICENSE 欄と data/corpus/SOURCES.md を参照。
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="$ROOT/data/corpus"

# tier | 出力名 | ライセンス | URL
ENTRIES='
core|ja-progit.epub|CC BY-NC-SA 3.0|https://github.com/progit/progit2-ja/releases/download/2.1.7/progit.epub
core|ja-progit.pdf|CC BY-NC-SA 3.0|https://github.com/progit/progit2-ja/releases/download/2.1.7/progit.pdf
core|en-progit.epub|CC BY-NC-SA 3.0|https://github.com/progit/progit2/releases/download/2.1.450/progit.epub
core|en-progit.pdf|CC BY-NC-SA 3.0|https://github.com/progit/progit2/releases/download/2.1.450/progit.pdf
core|en-eloquentjs.epub|CC BY-NC 3.0|https://eloquentjavascript.net/Eloquent_JavaScript.epub
core|en-eloquentjs.pdf|CC BY-NC 3.0|https://eloquentjavascript.net/Eloquent_JavaScript.pdf
ja|ja-johotsushin-r07.pdf|政府標準利用規約|https://www.soumu.go.jp/johotsusintokei/whitepaper/ja/r07/pdf/00zentai.pdf
ja|ja-ipa-websecurity.pdf|IPA 利用条件|https://www.ipa.go.jp/security/vuln/websecurity/ug65p900000196e2-att/000017316.pdf
ja|ja-ipa-sql.pdf|IPA 利用条件|https://www.ipa.go.jp/security/vuln/websecurity/ug65p900000196e2-att/000017320.pdf
en|en-mml-book.pdf|著者配布（個人利用）|https://mml-book.github.io/book/mml-book.pdf
en|en-d2l.pdf|CC BY-SA 4.0|https://d2l.ai/d2l-en.pdf
en|en-think-python.pdf|CC BY-NC 4.0|https://greenteapress.com/thinkpython2/thinkpython2.pdf
en|en-tlcl.pdf|CC BY-NC-ND 3.0|https://sourceforge.net/projects/linuxcommand/files/TLCL/19.01/TLCL-19.01.pdf/download
epub|en-alice.epub|Public Domain|https://www.gutenberg.org/ebooks/11.epub.images
epub|en-frankenstein.epub|Public Domain|https://www.gutenberg.org/ebooks/84.epub.images
'

rows() { echo "$ENTRIES" | grep -v '^[[:space:]]*$'; }

if [ "${1:-}" = "--list" ]; then
  printf '%-6s %-28s %-22s %s\n' TIER FILE LICENSE URL
  rows | while IFS='|' read -r tier name lic url; do
    printf '%-6s %-28s %-22s %s\n' "$tier" "$name" "$lic" "$url"
  done
  exit 0
fi

TIERS="${*:-core}"
[ "$TIERS" = "all" ] && TIERS="core ja en epub"

mkdir -p "$DEST"
ok=0; skip=0; fail=0
for tier in $TIERS; do
  rows | grep "^$tier|" | while IFS='|' read -r _ name lic url; do
    out="$DEST/$name"
    if [ -s "$out" ]; then
      echo "skip  $name ($(du -h "$out" | cut -f1))"
      continue
    fi
    echo "get   $name <- $url"
    if curl -fL --retry 3 --retry-delay 2 -m 900 -o "$out.part" "$url"; then
      mv "$out.part" "$out"
      echo "  ok  $name ($(du -h "$out" | cut -f1))"
    else
      rm -f "$out.part"
      echo "  NG  $name — 取得できなかった。URL が変わっている可能性がある" >&2
    fi
  done
done

# 取得したものの記録（ライセンス・入手元・入手日）
{
  echo "# 題材の入手元"
  echo
  echo "\`scripts/fetch-corpus.sh\` が生成。入手日: $(date +%Y-%m-%d)"
  echo
  echo "| ファイル | 大きさ | ライセンス | 入手元 |"
  echo "|---|---:|---|---|"
  rows | while IFS='|' read -r _ name lic url; do
    [ -s "$DEST/$name" ] || continue
    echo "| \`$name\` | $(du -h "$DEST/$name" | cut -f1) | $lic | <$url> |"
  done
} > "$DEST/SOURCES.md"

echo
echo "保存先: $DEST"
ls -1sh "$DEST" | tail -n +2
echo
echo "記録: $DEST/SOURCES.md"
