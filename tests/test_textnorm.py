from sensei.textnorm import collapse_whitespace, join_lines, normalize_text, search_key


def test_join_lines_japanese_without_space():
    assert join_lines(["リポジトリ", "のすべて"]) == "リポジトリのすべて"


def test_join_lines_mixed_and_english():
    assert join_lines(["Git の", "ブランチ"]) == "Git のブランチ"
    assert join_lines(["hello", "world"]) == "hello world"


def test_join_lines_hyphen_kept_unless_dehyphenate_says_so():
    assert join_lines(["fast-", "forward"]) == "fast-forward"
    assert join_lines(["reposi-", "tory"], lambda a, b: a + b == "repository") == "repository"


def test_normalize_text_spaces_after_japanese_punctuation():
    assert normalize_text("です。 Git は便利") == "です。Git は便利"
    assert normalize_text("ﬁle​") == "file"


def test_collapse_whitespace_html_newlines():
    assert collapse_whitespace("本書は\n  入門書です。\nGit") == "本書は入門書です。Git"


def test_search_key_nfkc():
    assert search_key("Ｇｉｔ の 基本") == search_key("Gitの基本")
