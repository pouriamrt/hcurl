"""Terminal chrome around a response: theme, header card, padded body."""

from dataclasses import dataclass
from http import HTTPStatus

from rich.box import ROUNDED
from rich.console import Console, Group, RenderableType
from rich.padding import Padding
from rich.panel import Panel
from rich.text import Text
from rich.theme import Theme

MAX_WIDTH = 110  # long lines past ~100 columns get hard to read
MUTED = "#565f89"
STATUS_COLORS = {2: "#9ece6a", 3: "#e0af68", 4: "#f7768e", 5: "#f7768e"}

# Tokyo Night-ish palette: readable on dark terminals, rich downgrades on 256-color ones.
THEME = Theme(
    {
        "markdown.link": "#7aa2f7",
        "markdown.link_url": "#7aa2f7",
        "markdown.h1": "bold #bb9af7",
        "markdown.h1.border": "#bb9af7",
        "markdown.h2": "bold #7dcfff",
        "markdown.h3": "bold #9ece6a",
        "markdown.h4": "italic #9ece6a",
        "markdown.strong": "bold #c0caf5",
        "markdown.em": "italic #c0caf5",
        "markdown.code": "#e0af68 on #1f2335",
        "markdown.code_block": "on #1f2335",
        "markdown.block_quote": "italic #a9b1d6",
        "markdown.item.bullet": "bold #bb9af7",
        "markdown.item.number": "#bb9af7",
        "markdown.hr": MUTED,
        "markdown.table.border": "#414868",
        "markdown.table.header": "bold #7dcfff",
        "json.key": "bold #7dcfff",
        "json.str": "#9ece6a",
        "json.number": "#ff9e64",
        "json.bool_true": "#bb9af7",
        "json.bool_false": "#bb9af7",
        "json.null": "italic #bb9af7",
        "json.brace": "#a9b1d6",
    }
)


@dataclass(frozen=True)
class Meta:
    """What the header card shows about a response."""

    status: str = ""  # e.g. "HTTP/1.1 200 OK" or "HTTP/2 404"
    content_type: str = ""
    url: str = ""
    size: int = 0
    elapsed: float | None = None  # seconds


def make_console() -> Console:
    console = Console(theme=THEME)
    console.width = min(console.width, MAX_WIDTH)
    return console


def status_code(status: str) -> int | None:
    parts = status.split()
    return int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None


def status_badge(status: str) -> Text:
    code = status_code(status)
    if code is None:
        return Text("")
    parts = status.split(maxsplit=2)
    if len(parts) > 2:
        reason = parts[2]
    else:  # HTTP/2 status lines carry no reason phrase
        try:
            reason = HTTPStatus(code).phrase
        except ValueError:
            reason = ""
    color = STATUS_COLORS.get(code // 100, MUTED)
    return Text(
        f" {code} {reason} ".replace("  ", " "), style=f"bold #1a1b26 on {color}"
    )


def human_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / 1024 / 1024:.1f} MB"


def header(meta: Meta, title: str = "") -> Panel:
    mime = meta.content_type.split(";")[0].strip()
    details = [mime, human_size(meta.size)] if mime else [human_size(meta.size)]
    if meta.elapsed is not None:
        details.append(f"{meta.elapsed * 1000:.0f} ms")
    line = Text.assemble(status_badge(meta.status), "  ", (" · ".join(details), MUTED))
    body = Group(line, Text(meta.url, style=f"italic {MUTED}")) if meta.url else line
    code = status_code(meta.status)
    border = STATUS_COLORS.get(code // 100, MUTED) if code else MUTED
    return Panel(
        body,
        title=Text(f" {title} ", style="bold #c0caf5") if title else None,
        title_align="left",
        box=ROUNDED,
        border_style=border,
        padding=(0, 1),
    )


def page(meta: Meta, title: str, body: RenderableType) -> Group:
    return Group(header(meta, title), Padding(body, (1, 2, 0, 2)))
