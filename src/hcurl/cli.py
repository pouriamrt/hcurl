"""hcurl: run curl, then render HTML/JSON responses with rich."""

import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from rich.console import Console
from rich.text import Text

from hcurl.render import is_binary, render
from hcurl.view import MUTED, Meta, header, human_size, make_console, page

# Flags that change what curl writes to stdout; with these hcurl stays out of the way.
PASSTHROUGH_LONG = {
    "--output",
    "--remote-name",
    "--remote-name-all",
    "--head",
    "--include",
    "--verbose",
    "--dump-header",
    "--help",
    "--version",
    "--trace",
    "--trace-ascii",
    "--write-out",
    "--cookie-jar",
    "--stderr",
    "--next",
}
PASSTHROUGH_SHORT = set("oOIivDhVwc:")
# Short options that take a value: the rest of a cluster after one is that value.
VALUE_SHORT = set("AbCdeEFHKmPQrtTuUxXyYz")
# "-u name" without ":password" makes curl ask for the password on stderr.
USER_FLAG = re.compile(r"-[uU](.*)|--(?:proxy-)?user(?:=(.*))?")
# A client key may be encrypted: OpenSSL then asks for its passphrase unless --pass.
KEY_FLAG = re.compile(r"-E.*|--(?:proxy-)?(?:cert|key)(?:=.*)?")
# "-T -", "-d @-", "-F f=<-": the request body is read from stdin.
READS_STDIN = re.compile(r"-|.*[@<]-")


def wants_passthrough(args: list[str]) -> bool:
    for arg in args:
        if arg.split("=", 1)[0] in PASSTHROUGH_LONG:
            return True
        if arg.startswith("-") and not arg.startswith("--"):
            for flag in arg[1:]:  # "-sSLo", "-ofile", "-Hx-o: y"
                if flag in PASSTHROUGH_SHORT:
                    return True
                if flag in VALUE_SHORT:
                    break
    return False


def needs_tty(args: list[str], stdin_is_tty: bool) -> bool:
    """True when curl may talk to the user, so it gets the terminal and no spinner."""
    # shortcut: misses "-u"/"-E" inside a cluster like "-su name"; that prompt shows
    # late (after the spinner). Parse clusters like wants_passthrough if it bites.
    if stdin_is_tty and any(READS_STDIN.fullmatch(arg) for arg in args):
        return True
    has_pass = any(arg.split("=", 1)[0] == "--pass" for arg in args)
    if not has_pass and any(KEY_FLAG.fullmatch(arg) for arg in args):
        return True
    for i, arg in enumerate(args):
        match = USER_FLAG.fullmatch(arg)
        if match is None:
            continue
        value = match.group(1) or match.group(2)
        if not value:
            value = args[i + 1] if i + 1 < len(args) else ""
        if ":" not in value:
            return True
    return False


def parse_headers(raw: str) -> tuple[str, dict[str, str]]:
    """Status line and lowercased headers of the final response (after redirects)."""
    # Only status blocks count: chunked trailers arrive as a trailing header-only block.
    blocks = [b for b in re.split(r"\r?\n\r?\n", raw) if b.lstrip().startswith("HTTP/")]
    if not blocks:
        return "", {}
    status, *lines = blocks[-1].splitlines()
    headers = {}
    for line in lines:
        name, sep, value = line.partition(":")
        if sep:
            headers[name.strip().lower()] = value.strip()
    return status.strip(), headers


def show(body: bytes, meta: Meta, console: Console) -> None:
    rendered = render(body, meta.content_type, meta.url)
    if rendered is not None:
        console.print(page(meta, rendered.title, rendered.body))
        return
    if meta.status:
        console.print(header(meta))
    if is_binary(body):
        console.print(
            f"[#e0af68]Binary body ({human_size(len(body))}) not printed. "
            "Use curl -o <file> to save it.[/]"
        )
        return
    sys.stdout.buffer.write(body)
    sys.stdout.flush()


def url_of(args: list[str]) -> str:
    return next((a for a in args if re.match(r"[a-zA-Z][\w+.-]*://", a)), "")


def stdout_is_tty() -> bool:
    return sys.stdout.isatty()


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows defaults to cp1252
    console = make_console()

    if args == ["-"]:  # render a body piped in: curl ... | hcurl -
        body = sys.stdin.buffer.read()
        show(body, Meta(size=len(body)), console)
        return 0

    curl = shutil.which("curl")
    if curl is None:
        console.print("[red]hcurl: curl not found on PATH[/]")
        return 127
    if wants_passthrough(args) or not stdout_is_tty():
        return subprocess.call([curl, *args])

    with tempfile.TemporaryDirectory() as tmp:
        header_file = Path(tmp) / "headers"
        url = url_of(args)
        command = [curl, "-sS", "-D", str(header_file), *args]
        started = time.perf_counter()
        if needs_tty(args, sys.stdin.isatty()):
            proc = subprocess.run(command, stdout=subprocess.PIPE, check=False)
        else:
            # Text, not a markup string: a URL like ".../[red]x" must print as-is.
            spinner = Text(f"Fetching {url}" if url else "Fetching", style=MUTED)
            # One line only: the Spinner re-assembles the text, dropping no_wrap.
            spinner.truncate(console.width - 2, overflow="ellipsis")  # 2 = frame + gap
            with console.status(spinner, spinner_style=MUTED):
                proc = subprocess.run(
                    command,
                    capture_output=True,  # curl's stderr would garble the spinner
                    check=False,  # curl exit code is returned to the caller
                )
            if proc.stderr:
                sys.stderr.buffer.write(proc.stderr)
                sys.stderr.flush()
        elapsed = time.perf_counter() - started  # includes curl startup, ~tens of ms
        raw_headers = (
            header_file.read_text(errors="replace") if header_file.exists() else ""
        )

    status, headers = parse_headers(raw_headers)
    if proc.stdout:
        meta = Meta(
            status=status,
            content_type=headers.get("content-type", ""),
            url=url,
            size=len(proc.stdout),
            elapsed=elapsed,
        )
        show(proc.stdout, meta, console)
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
