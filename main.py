"""markterm — render Markdown as beautiful terminal output using Rich.

Reads a Markdown file (or piped stdin) and prints a styled, syntax-highlighted
rendering to the terminal — including images (drawn with half-block characters)
and Mermaid flowcharts (rendered as ASCII art).

Examples:
    markterm README.md
    cat notes.md | markterm
    markterm docs.md --pager --code-theme dracula
"""

import argparse
import errno
import io
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

from rich.align import Align
from rich.color import Color
from rich.console import Console
from rich.markdown import CodeBlock, ImageItem, Markdown
from rich.measure import Measurement
from rich.segment import Segment
from rich.style import Style
from rich.text import Text

__version__ = "0.1.0"

DEFAULT_CODE_THEME = "monokai"
DEFAULT_IMAGE_WIDTH = 50  # maximum image size, in terminal cells


# ---------------------------------------------------------------------------
# Image support
# ---------------------------------------------------------------------------

_IMG_TAG_RE = re.compile(r"<img\b([^>]*?)/?>", re.IGNORECASE)
_BLOCK_WRAPPER_RE = re.compile(
    r"</?(?:p|div|center|figure|picture)\b[^>]*>", re.IGNORECASE
)


def _html_attr(attrs: str, name: str) -> str:
    """Extract an HTML attribute value from a tag's attribute string."""
    match = re.search(
        rf"""\b{name}\s*=\s*("([^"]*)"|'([^']*)')""", attrs, re.IGNORECASE
    )
    if match is None:
        return ""
    return match.group(2) if match.group(2) is not None else (match.group(3) or "")


def html_images_to_markdown(markup: str) -> str:
    """Rewrite raw HTML ``<img>`` tags as Markdown ``![alt](src)``.

    READMEs frequently center images with
    ``<p align="center"><img src="..."></p>``. markdown-it treats that whole
    block as opaque HTML and Rich renders nothing, so we translate the image to
    Markdown syntax and drop the surrounding block wrappers.
    """

    def replace(match: "re.Match[str]") -> str:
        attrs = match.group(1)
        src = _html_attr(attrs, "src")
        if not src:
            return match.group(0)
        return f"\n\n![{_html_attr(attrs, 'alt')}]({src})\n\n"

    markup = _IMG_TAG_RE.sub(replace, markup)
    return _BLOCK_WRAPPER_RE.sub("", markup)


def load_image(src: str, base_path: Path | None):
    """Load a PIL image from a URL or local path; return None on any failure."""
    try:
        from PIL import Image
    except ImportError:
        return None

    try:
        if src.startswith(("http://", "https://")):
            request = urllib.request.Request(src, headers={"User-Agent": "markterm"})
            with urllib.request.urlopen(request, timeout=15) as response:
                data = response.read()
            image = Image.open(io.BytesIO(data))
        else:
            path = Path(src)
            if not path.is_absolute() and base_path is not None:
                path = base_path / path
            image = Image.open(path)
        image.load()  # force-read pixel data so access can't fail lazily later
        return image
    except Exception:
        # Network error, missing file, unsupported format, ... — fall back to
        # the textual placeholder rather than crashing the whole render.
        return None


# Emoji (and the variation selector / ZWJ that follow them) are width-2 but
# mermaid-ascii counts them as width-1, which skews its box borders. Strip them
# for clean ASCII output. Plain arrows like → (U+2192) are intentionally kept.
_EMOJI_RE = re.compile(
    "[\U0001f000-\U0001faff\u2600-\u27bf\U0001f1e6-\U0001f1ff\ufe0f\u200d]"
)


def mermaid_for_ascii(diagram: str) -> str:
    """Adapt a Mermaid flowchart so mermaid-ascii can render it.

    mermaid-ascii doesn't support subgraphs, HTML labels, or non-rectangular
    node shapes, so we drop the subgraph wrappers, strip HTML and emoji from
    labels, flatten every node shape to a rectangle, and normalize edge styles.
    """
    diagram = diagram.replace("\r\n", "\n").replace("\r", "\n")
    kept = []
    for line in diagram.splitlines():
        low = line.strip().lower()
        if low.startswith("subgraph") or low == "end" or low.startswith("direction"):
            continue
        kept.append(line)
    text = "\n".join(kept)

    text = re.sub(r"<br\s*/?>", " ", text, flags=re.IGNORECASE)  # <br/> -> space
    text = re.sub(r"</?[a-zA-Z][^>]*>", "", text)                # drop other HTML tags
    text = _EMOJI_RE.sub("", text)

    # Flatten node shapes to rectangles (double-delimiters before single ones).
    text = re.sub(r"\(\[(.*?)\]\)", r"[\1]", text)   # ([stadium])
    text = re.sub(r"\[\((.*?)\)\]", r"[\1]", text)   # [(cylinder)]
    text = re.sub(r"\(\((.*?)\)\)", r"[\1]", text)   # ((circle))
    text = re.sub(r"\{\{(.*?)\}\}", r"[\1]", text)   # {{hexagon}}
    text = re.sub(r"\{(.*?)\}", r"[\1]", text)        # {rhombus}

    text = re.sub(r"<?[-=.]{2,}>", "-->", text)       # ==>, -.->, <-.-> -> -->

    # Tidy whitespace left behind by removals.
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\[ +", "[", text)
    text = re.sub(r" +\]", "]", text)
    return text


def render_mermaid_ascii(diagram: str) -> str | None:
    """Render a Mermaid flowchart as ASCII/Unicode art via ``mermaid-ascii``.

    Returns None when mermaid-ascii isn't installed or fails, so the caller can
    fall back to the diagram source. Runs locally and instantly (no browser),
    and works even when output isn't a color terminal. The diagram is fed over
    stdin as raw bytes to avoid Windows newline translation (mermaid-ascii
    rejects a stray ``\\r``).
    """
    binary = shutil.which("mermaid-ascii")
    if binary is None:
        return None
    payload = mermaid_for_ascii(diagram).encode("utf-8")
    try:
        result = subprocess.run([binary], input=payload, capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    output = result.stdout.decode("utf-8", errors="replace")
    return "\n".join(line.rstrip() for line in output.splitlines())


class PixelImage:
    """Render a PIL image to the terminal using half-block characters.

    Each cell stacks two pixels: the upper half block ``▀`` is coloured as the
    top pixel (foreground) over the bottom pixel (background). Transparent
    pixels are left blank so the terminal background shows through.
    """

    UPPER_HALF = "▀"
    LOWER_HALF = "▄"
    ALPHA_THRESHOLD = 128

    def __init__(self, image, max_width: int, max_height: int) -> None:
        self.image = image.convert("RGBA")
        self.max_width = max_width
        self.max_height = max_height

    def _target_cells(self, available_width: int) -> tuple[int, int]:
        """Pixel dimensions to resize to (width cells, height in pixels)."""
        max_w = max(1, min(self.max_width, available_width))
        # Each cell is 1 pixel wide and 2 pixels tall, which keeps pixels
        # roughly square on a typical terminal. Scale to fit without enlarging.
        scale = min(
            max_w / self.image.width,
            (self.max_height * 2) / self.image.height,
            1.0,
        )
        width = max(1, round(self.image.width * scale))
        height = max(2, round(self.image.height * scale))
        return width, height

    def __rich_measure__(self, console: Console, options) -> Measurement:
        width, _ = self._target_cells(options.max_width)
        return Measurement(width, width)

    def __rich_console__(self, console: Console, options):
        from PIL import Image

        width, height = self._target_cells(options.max_width)
        resized = self.image.resize((width, height), Image.Resampling.LANCZOS)
        pixels = resized.load()
        rgb = Color.from_rgb

        for y in range(0, height, 2):
            segments = []
            for x in range(width):
                tr, tg, tb, ta = pixels[x, y]
                if y + 1 < height:
                    br, bg, bb, ba = pixels[x, y + 1]
                else:
                    br = bg = bb = ba = 0
                top = ta >= self.ALPHA_THRESHOLD
                bottom = ba >= self.ALPHA_THRESHOLD
                if top and bottom:
                    style = Style(color=rgb(tr, tg, tb), bgcolor=rgb(br, bg, bb))
                    segments.append(Segment(self.UPPER_HALF, style))
                elif top:
                    segments.append(Segment(self.UPPER_HALF, Style(color=rgb(tr, tg, tb))))
                elif bottom:
                    segments.append(Segment(self.LOWER_HALF, Style(color=rgb(br, bg, bb))))
                else:
                    segments.append(Segment(" "))
            segments.append(Segment.line())
            yield from segments


class MermaidCodeBlock(CodeBlock):
    """A fenced code block that renders ```mermaid blocks as ASCII diagrams.

    Non-mermaid blocks render normally (syntax highlighted). Mermaid blocks are
    drawn as ASCII/Unicode art via mermaid-ascii, falling back to the
    highlighted source when it isn't available or can't parse the diagram.
    """

    @classmethod
    def create(cls, markdown: Markdown, token) -> "MermaidCodeBlock":
        node_info = token.info or ""
        lexer_name = node_info.partition(" ")[0]
        instance = cls(lexer_name or "text", markdown.code_theme)
        instance.markdown = markdown
        return instance

    def __rich_console__(self, console: Console, options):
        markdown = getattr(self, "markdown", None)
        if self.lexer_name.lower() == "mermaid" and getattr(markdown, "show_mermaid", True):
            ascii_art = render_mermaid_ascii(str(self.text))
            if ascii_art:
                # Keep the diagram's fixed-width layout intact: never wrap,
                # crop overflow rather than reflow on narrow terminals.
                yield Text.from_ansi(ascii_art, no_wrap=True, overflow="crop")
                return
        yield from super().__rich_console__(console, options)


class TerminalImageItem(ImageItem):
    """Image element that draws the real image, with a text fallback.

    Falls back to Rich's placeholder when images are disabled, the image can't
    be loaded, or the output can't show colour (piped output, --no-color).
    """

    @classmethod
    def create(cls, markdown: Markdown, token) -> "TerminalImageItem":
        item = cls(str(token.attrs.get("src", "")), markdown.hyperlinks)
        item.markdown = markdown
        return item

    def __rich_console__(self, console: Console, options):
        markdown = getattr(self, "markdown", None)
        can_show = (
            getattr(markdown, "show_images", True)
            and console.is_terminal
            and not console.no_color
            and console.color_system in {"standard", "256", "truecolor", "windows"}
        )
        if can_show:
            image = load_image(self.destination, getattr(markdown, "base_path", None))
            if image is not None:
                limit = getattr(markdown, "image_width", DEFAULT_IMAGE_WIDTH)
                yield Align.center(PixelImage(image, limit, limit))
                return
        yield from super().__rich_console__(console, options)


class ImageMarkdown(Markdown):
    """Markdown that renders images and Mermaid diagrams using half blocks."""

    elements = {
        **Markdown.elements,
        "image": TerminalImageItem,
        "fence": MermaidCodeBlock,
    }

    def __init__(
        self,
        markup: str,
        *,
        base_path: Path | None = None,
        image_width: int = DEFAULT_IMAGE_WIDTH,
        show_images: bool = True,
        show_mermaid: bool = True,
        **kwargs,
    ) -> None:
        super().__init__(html_images_to_markdown(markup), **kwargs)
        self.base_path = base_path
        self.image_width = image_width
        self.show_images = show_images
        self.show_mermaid = show_mermaid


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser."""
    parser = argparse.ArgumentParser(
        prog="markterm",
        description="Render Markdown as beautiful, syntax-highlighted terminal output.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  markterm README.md\n"
            "  cat notes.md | markterm\n"
            "  markterm docs.md --pager --code-theme dracula\n"
        ),
    )
    parser.add_argument(
        "file",
        nargs="?",
        help="Markdown file to render; reads from stdin if omitted or '-'.",
    )
    parser.add_argument(
        "-t",
        "--code-theme",
        default=DEFAULT_CODE_THEME,
        metavar="THEME",
        help=f"Pygments theme for fenced code blocks (default: {DEFAULT_CODE_THEME}).",
    )
    parser.add_argument(
        "--inline-code-theme",
        metavar="THEME",
        help="Pygments theme for `inline code` (default: plain styling).",
    )
    parser.add_argument(
        "-w",
        "--width",
        type=int,
        metavar="N",
        help="Render width in columns (default: terminal width).",
    )
    parser.add_argument(
        "--image-width",
        type=int,
        default=DEFAULT_IMAGE_WIDTH,
        metavar="N",
        help=f"Max image width in terminal columns (default: {DEFAULT_IMAGE_WIDTH}).",
    )
    parser.add_argument(
        "--no-images",
        action="store_true",
        help="Don't draw images; show a text placeholder instead.",
    )
    parser.add_argument(
        "--no-mermaid",
        action="store_true",
        help="Don't render Mermaid diagrams; show the diagram source instead.",
    )
    parser.add_argument(
        "-p",
        "--pager",
        action="store_true",
        help="Page long output through a scrolling pager.",
    )
    parser.add_argument(
        "--justify",
        choices=("default", "left", "center", "right", "full"),
        default="default",
        help="Paragraph justification (default: as written).",
    )
    parser.add_argument(
        "--no-hyperlinks",
        action="store_true",
        help="Render links as plain text instead of clickable hyperlinks.",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable colors and styling.",
    )
    parser.add_argument(
        "--force-color",
        action="store_true",
        help="Force colored output even when piping to another program.",
    )
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


def theme_exists(name: str | None) -> bool:
    """Return True if ``name`` is a known Pygments style (or None)."""
    if name is None:
        return True
    from pygments.styles import get_style_by_name
    from pygments.util import ClassNotFound

    try:
        get_style_by_name(name)
        return True
    except ClassNotFound:
        return False


def force_utf8_io() -> None:
    """Make stdout/stderr emit UTF-8.

    On Windows the standard streams often default to a legacy code page
    (e.g. cp1252) that cannot encode the box-drawing characters and emoji
    Rich uses, which would otherwise crash with a UnicodeEncodeError.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def _silence_stdout() -> None:
    """Point stdout at the null device.

    Called after a broken pipe so the interpreter's final flush on shutdown
    doesn't raise a second error ("Exception ignored on flushing sys.stdout").
    """
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
    except OSError:
        pass


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    force_utf8_io()

    out = Console(
        width=args.width,
        no_color=args.no_color,
        force_terminal=args.force_color or None,
    )
    err = Console(stderr=True, style="bold red")

    # Validate code themes up front so a typo fails cleanly instead of
    # dumping a traceback halfway through rendering.
    for theme in (args.code_theme, args.inline_code_theme):
        if not theme_exists(theme):
            err.print(f"markterm: unknown code theme '{theme}'")
            return 1

    # Resolve the input source: explicit file, or piped stdin. base_path is
    # used to resolve relative image paths.
    if args.file in (None, "-"):
        if sys.stdin.isatty():
            parser.print_help(sys.stderr)
            return 2
        try:
            sys.stdin.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
        text = sys.stdin.read()
        base_path = Path.cwd()
    else:
        path = Path(args.file)
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            err.print(f"markterm: file not found: {args.file}")
            return 1
        except IsADirectoryError:
            err.print(f"markterm: not a file: {args.file}")
            return 1
        except OSError as exc:
            err.print(f"markterm: cannot read {args.file}: {exc}")
            return 1
        base_path = path.resolve().parent

    markdown = ImageMarkdown(
        text,
        base_path=base_path,
        image_width=args.image_width,
        show_images=not args.no_images,
        show_mermaid=not args.no_mermaid,
        code_theme=args.code_theme,
        inline_code_theme=args.inline_code_theme,
        justify=args.justify,
        hyperlinks=not args.no_hyperlinks,
    )

    try:
        if args.pager:
            with out.pager(styles=True):
                out.print(markdown)
        else:
            out.print(markdown)
    except KeyboardInterrupt:
        return 130
    except (BrokenPipeError, OSError) as exc:
        # A downstream reader closed the pipe early (e.g. `| head`, or the
        # user quit the pager). Exit quietly instead of dumping a traceback.
        # On Windows a broken pipe surfaces as EINVAL rather than EPIPE.
        if isinstance(exc, BrokenPipeError) or exc.errno in (errno.EPIPE, errno.EINVAL):
            _silence_stdout()
            return 0
        raise

    return 0


if __name__ == "__main__":
    sys.exit(main())
