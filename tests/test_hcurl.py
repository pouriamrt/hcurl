import io

import pytest
from hypothesis import given
from hypothesis import strategies as st
from rich.console import Console, RenderableType

from hcurl import cli
from hcurl.render import html_to_markdown, kind_of, render
from hcurl.view import Meta, header, human_size, status_badge

PAGE = b"""<!DOCTYPE html><html><head><title>Demo</title><style>body{color:red}</style>
<script>var secret = 1</script></head><body><h1>Hello</h1>
<p>Some <b>bold</b> and a <a href="/x">link</a>.</p><ul><li>one</li><li>two</li></ul>
<table><tr><th>Name</th><th>Age</th></tr><tr><td>Ann</td><td>30</td></tr></table>
</body></html>"""


def body_text(body: bytes, content_type: str) -> str:
    rendered = render(body, content_type)
    assert rendered is not None
    return text_of(rendered.body)


def text_of(renderable: RenderableType) -> str:
    console = Console(record=True, width=80, file=io.StringIO())
    console.print(renderable)
    return console.export_text()


@pytest.mark.parametrize(
    ("content_type", "body", "kind"),
    [
        ("text/html; charset=utf-8", b"anything", "html"),
        ("application/problem+json", b"{}", "json"),
        ("text/plain", b"<html>", "raw"),
        ("", PAGE, "html"),
        ("", b' [1, {"a": 2}]', "json"),
        ("", b"{not json", "raw"),
        ("", b"", "raw"),
    ],
)
def test_kind_of(content_type, body, kind):
    assert kind_of(content_type, body) == kind


def test_html_to_markdown_drops_noise_keeps_structure():
    title, md = html_to_markdown(PAGE.decode())
    assert title == "Demo"
    assert "secret" not in md and "color:red" not in md
    assert "# Hello" in md and "[link](/x)" in md and "| Ann | 30 |" in md


def test_render_html_shows_title_and_table():
    rendered = render(PAGE, "text/html")
    assert rendered is not None and rendered.title == "Demo"
    out = text_of(rendered.body)
    assert "Hello" in out and "Ann" in out
    assert "secret" not in out


def test_render_empty_html_hints_at_javascript():
    out = body_text(b"<html><body><div id=root></div></body></html>", "text/html")
    assert "JavaScript" in out


def test_render_respects_charset():
    body = "café".encode("latin-1")
    out = body_text(b'{"x": "' + body + b'"}', "application/json; charset=latin-1")
    assert "café" in out


def test_render_raw_returns_none():
    assert render(b"plain", "text/plain") is None


def test_parse_headers_takes_last_block_after_redirect():
    raw = (
        "HTTP/1.1 301 Moved\r\nLocation: /b\r\n\r\n"
        "HTTP/2 200\r\nContent-Type: text/html\r\nX-A: b:c\r\n\r\n"
    )
    status, headers = cli.parse_headers(raw)
    assert status == "HTTP/2 200"
    assert headers == {"content-type": "text/html", "x-a": "b:c"}
    assert cli.parse_headers("") == ("", {})


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["-o", "f", "u"], True),
        (["-sSLo", "f", "u"], True),
        (["--output=f", "u"], True),
        (["-I", "u"], True),
        (["-sSL", "-H", "Accept: x", "u"], False),
        (["-X", "POST", "-d", "a=1", "u"], False),
    ],
)
def test_wants_passthrough(args, expected):
    assert cli.wants_passthrough(args) is expected


def test_main_renders_file_url_with_real_curl(tmp_path, monkeypatch, capsys):
    page = tmp_path / "page.html"
    page.write_bytes(PAGE)
    monkeypatch.setattr(cli, "stdout_is_tty", lambda: True)
    assert cli.main([page.as_uri()]) == 0
    out = capsys.readouterr().out
    assert "Demo" in out and "Hello" in out and "Ann" in out and "secret" not in out


def test_main_passes_curl_exit_code(monkeypatch):
    monkeypatch.setattr(cli, "stdout_is_tty", lambda: True)
    assert cli.main(["file:///definitely/missing/file.html"]) != 0


@given(st.text(), st.binary())
def test_kind_of_total(content_type: str, body: bytes) -> None:
    assert kind_of(content_type, body) in {"html", "json", "raw"}


@given(st.text())
def test_parse_headers_never_raises(raw: str) -> None:
    _status, headers = cli.parse_headers(raw)
    assert all(name == name.lower() for name in headers)


def test_layout_tables_are_flattened_data_tables_kept():
    layout = "<table><tr><td><a href='/i'>Item one</a></td></tr></table>"
    _, md = html_to_markdown(layout)
    assert "|" not in md and "[Item one](/i)" in md
    _, md = html_to_markdown(PAGE.decode())
    assert "| Ann | 30 |" in md


# Regression pins for code-review findings.
@pytest.mark.parametrize("body", [b"", b" ", b"not json", b'{"a": 1}\n{"b": 2}'])
def test_invalid_json_falls_back_to_raw(body: bytes) -> None:
    assert render(body, "application/json") is None


def test_title_markup_is_not_interpreted() -> None:
    page = b"<html><head><title>Foo [/bar] [red]x</title></head><body>hi</body></html>"
    rendered = render(page, "text/html")
    assert rendered is not None
    assert "Foo [/bar] [red]x" in text_of(
        header(Meta(status="HTTP/1.1 200 OK"), rendered.title)
    )


def test_escape_sequences_are_stripped() -> None:
    page = b"<html><body><p>a\x1b]0;pwned\x07b\x1b[31mc</p></body></html>"
    out = body_text(page, "text/html")
    assert "\x1b" not in out and "\x07" not in out and "a]0;pwnedb[31mc" in out


def test_utf8_bom_json() -> None:
    assert "café" in body_text('﻿{"x": "café"}'.encode(), "application/json")


def test_parse_headers_ignores_trailer_block() -> None:
    raw = "HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\nX-Checksum: abc\r\n\r\n"
    assert cli.parse_headers(raw) == ("HTTP/1.1 200 OK", {"content-type": "text/html"})


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["-w", "%{http_code}", "u"], True),
        (["--write-out=%{http_code}", "u"], True),
        (["-ofile", "u"], True),
        (["-Dhdrs.txt", "u"], True),
        (["-c", "-", "u"], True),
        (["u1", "-:", "u2"], True),
        (["-HX-Info: o", "u"], False),
        (["-sSLHAccept: v", "u"], False),
    ],
)
def test_wants_passthrough_review_cases(args: list[str], expected: bool) -> None:
    assert cli.wants_passthrough(args) is expected


def test_cleanup_images_empty_links_separators_and_link_spacing() -> None:
    html = (
        '<span><b><a href="/">Hacker News</a></b><a href="/new">new</a> | '
        '<a href="/past">past</a> | <a href="/x"></a> | <a href="/y"> </a> | </span>'
        '<img src="y18.svg" alt="logo"><table><tr><th>A</th></tr><tr><td>1</td></tr></table>'
    )
    _, md = html_to_markdown(html)
    first_line = md.splitlines()[0]
    assert first_line == "**[Hacker News](/)** [new](/new) | [past](/past)"
    assert "y18" not in md and "logo" not in md
    assert "| A |" in md and "| 1 |" in md  # data tables untouched


@pytest.mark.parametrize(
    ("status", "badge"),
    [
        ("HTTP/1.1 200 OK", " 200 OK "),
        ("HTTP/2 404", " 404 Not Found "),
        ("HTTP/2 299", " 299 "),
        ("", ""),
    ],
)
def test_status_badge(status: str, badge: str) -> None:
    assert status_badge(status).plain == badge


def test_header_card_shows_meta() -> None:
    meta = Meta("HTTP/2 200", "text/html; charset=utf-8", "https://x.test/", 2048, 0.25)
    out = text_of(header(meta, "Title"))
    for part in ("Title", "200 OK", "text/html", "2.0 KB", "250 ms", "https://x.test/"):
        assert part in out
    assert human_size(5) == "5 B" and human_size(3 * 1024 * 1024) == "3.0 MB"


def test_link_text_equal_to_href_survives() -> None:
    out = body_text(
        b'<html><body><a href="ask">ask</a> | <a href="show">show</a></body></html>',
        "text/html",
    )
    assert "ask" in out and "show" in out


def test_tidy_collapses_spaces_but_not_in_code_or_tables() -> None:
    from hcurl.render import tidy

    md = "a  b   (c)\n\n```\nx  =  1\n```\n\n| k  | v |\n| --- | --- |"
    assert tidy(md) == "a b (c)\n\n```\nx  =  1\n```\n\n| k  | v |\n| --- | --- |"
