from utils.text_clean import normalize_whitespace, strip_html, truncate


def test_strip_html_removes_tags() -> None:
    html = "<div>Hello <b>World</b><script>bad()</script></div>"
    assert strip_html(html) == "Hello World"


def test_normalize_whitespace_collapses_runs() -> None:
    text = "Hello \n  World\t\t!"
    assert normalize_whitespace(text) == "Hello World !"


def test_truncate_appends_suffix_when_needed() -> None:
    text = "a" * 10
    assert truncate(text, max_len=5) == "aaaaa..."
