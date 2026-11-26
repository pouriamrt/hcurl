"""Turn an HTTP response body into rich renderables."""

import json
import mimetypes
import re
from typing import NamedTuple
from urllib.parse import urlsplit

from bs4 import BeautifulSoup, Tag
from markdownify import markdownify
from rich.console import RenderableType
from rich.json import JSON
from rich.markdown import Markdown
from rich.syntax import Syntax

# Tags whose content is never readable text in a terminal (media, widgets, forms).
NOISE_TAGS = (
    "script", "style", "noscript", "svg", "iframe", "template", "head",
    "img", "picture", "video", "audio", "canvas", "form", "button",
)  # fmt: skip
# Rows of separators left behind by removed nav items, e.g. "new |  |  |  |".
SEPARATOR_RUN = re.compile(r"(?:\s*\|){2,}\s*")
LAYOUT_TABLE_TAGS = ("thead", "tbody", "tfoot", "tr", "td")
HTML_TYPES = ("text/html", "application/xhtml+xml")
SNIFF_BYTES = 512
CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")  # keeps \t and \n
BINARY_PROBE = 1024
# Highlighting + wrapping costs ~0.5 s per 44 KB (3 s at 220 KB); past this, print raw.
HIGHLIGHT_LIMIT = 64 * 1024
CODE_THEME = "one-dark"
CODE_BACKGROUND = "#1f2335"  # same as inline code in view.THEME
# mimetypes has no extension for these, or a misleading one (application/xml -> .xsl).
MIME_EXTENSIONS = {
    "application/javascript": ".js",
    "image/svg+xml": ".xml",
    "application/xml": ".xml",
    "application/x-yaml": ".yaml",
    "text/yaml": ".yaml",
    "application/toml": ".toml",
    "application/sql": ".sql",
    "application/graphql": ".graphql",
}
PLAIN_LEXERS = ("default", "text")


def kind_of(content_type: str, body: bytes) -> str:
    """Classify a response as "html", "json" or "raw"."""
    mime = content_type.split(";")[0].strip().lower()
    if mime in HTML_TYPES:
        return "html"
    if mime == "application/json" or mime.endswith("+json"):
        return "json"
    if mime:
        return "raw"
    # No Content-Type (file://, stdin): sniff the start of the body.
    head = body[:SNIFF_BYTES].lstrip().lower()
    if head.startswith((b"<!doctype html", b"<html")):
        return "html"
    if head[:1] in (b"{", b"["):
        try:
            json.loads(body)
        except ValueError:
            return "raw"
        return "json"
    return "raw"


def is_binary(body: bytes) -> bool:
    return b"\x00" in body[:BINARY_PROBE]


def lexer_for(content_type: str, url: str, code: str) -> str | None:
    """Pygments lexer for a text body: declared type first, then the URL's extension."""
    mime = content_type.split(";")[0].strip().lower()
    extension = MIME_EXTENSIONS.get(mime) or mimetypes.guess_extension(mime) or ""
    # Declared type: by extension only. Content sniffing turns CSS into "css+genshi".
    lexer = Syntax.guess_lexer("body" + extension)
    if lexer in PLAIN_LEXERS:
        try:
            path = urlsplit(url).path
        except ValueError:  # curl glob like "http://host[1-3].com/" isn't a valid URL
            path = ""
        # URL extension: content breaks ties between lexers sharing it (.h: C/C++/ObjC).
        lexer = Syntax.guess_lexer(path, code)
    return None if lexer in PLAIN_LEXERS else lexer


def decode(body: bytes, content_type: str) -> str:
    """Decode with the declared charset; strip BOM and terminal control characters."""
    match = re.search(r"charset=[\"']?([\w-]+)", content_type, re.IGNORECASE)
    try:
        text = body.decode(match.group(1) if match else "utf-8-sig", "replace")
    except LookupError:  # unknown charset name from the server
        text = body.decode("utf-8-sig", "replace")
    # A hostile page could otherwise send ESC sequences (retitle, recolor, overwrite).
    return CONTROL_CHARS.sub("", text.lstrip("﻿"))


def html_to_markdown(html: str) -> tuple[str, str]:
    """Return (page title, markdown body) with scripts, styles and head removed."""
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else ""
    for tag in soup(NOISE_TAGS):
        tag.decompose()
    # Old-school sites (e.g. Hacker News) lay out pages with header-less tables;
    # rendered as markdown tables they turn into empty grids, so flatten them.
    for table in soup.find_all("table"):
        if table.find("th") is None:
            for cell in [table, *table.find_all(LAYOUT_TABLE_TAGS)]:
                cell.name = "div"
    for link in soup.find_all("a"):
        if not link.get_text(strip=True):
            link.decompose()
            continue
        # Browsers space touching links with CSS ("Hacker News" + "new"); text needs a gap.
        edge = link
        while edge.next_sibling is None and isinstance(edge.parent, Tag):
            edge = edge.parent
        if isinstance(edge.next_sibling, Tag):
            edge.insert_after(" ")
    markdown = markdownify(
        # autolinks would emit "<ask>" for href="ask"; rich drops it as an HTML tag.
        str(soup),
        heading_style="ATX",
        bullets="-",
        autolinks=False,
    )
    return title, tidy(markdown)


def tidy(markdown: str) -> str:
    """Drop separator debris, doubled spaces and blank-line runs; keep tables and code."""
    lines = []
    in_fence = False
    for line in markdown.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        elif not in_fence and not line.lstrip().startswith("|"):
            line = SEPARATOR_RUN.sub(" | ", line).rstrip(" |")
            line = re.sub(r"(?<=\S) {2,}(?=\S)", " ", line)
        lines.append(line.rstrip())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


class Rendered(NamedTuple):
    title: str
    body: RenderableType


def render(body: bytes, content_type: str, url: str = "") -> Rendered | None:
    """Title and rich renderable for the body, or None when it should be written raw."""
    kind = kind_of(content_type, body)
    text = decode(body, content_type)
    if kind == "json":
        try:
            return Rendered("", JSON(text))
        except ValueError:  # labelled JSON but isn't (empty, NDJSON, error page)
            return None
    if kind == "html":
        title, markdown = html_to_markdown(text)
        empty = "_(empty page: it may need JavaScript to render)_"
        return Rendered(title, Markdown(markdown or empty, code_theme=CODE_THEME))
    if is_binary(body) or len(body) > HIGHLIGHT_LIMIT:
        return None
    lexer = lexer_for(content_type, url, text)
    if lexer is None:
        return None
    return Rendered(
        "",
        Syntax(
            text,
            lexer,
            theme=CODE_THEME,
            word_wrap=True,
            background_color=CODE_BACKGROUND,
        ),
    )
