# markterm

> Read Markdown the way it's meant to look — right in your terminal.

![Python](https://img.shields.io/badge/Python-3.13+-3776AB?logo=python&logoColor=white)
![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)
![Built with Rich](https://img.shields.io/badge/built%20with-Rich-ff69b4)

**markterm** renders Markdown as beautiful, syntax-highlighted terminal output,
powered by [Rich](https://github.com/Textualize/rich). It goes beyond text:
it draws **images** inline (with Unicode half-blocks) and turns **Mermaid
flowcharts** into ASCII art — all locally, nothing uploaded.

```bash
markterm README.md
cat notes.md | markterm
```

<!-- Tip: add a screenshot or GIF of markterm rendering a colorful document here, e.g. assets/demo.png -->

Mermaid flowcharts become readable ASCII you can paste anywhere:

```text
┌──────────┐     ┌──────────┐     ┌──────────┐
│ Markdown ├────►│ markterm ├────►│ Terminal │
└──────────┘     └──────────┘     └──────────┘
```

## Features

- Headings, **bold**/*italic*/~~strikethrough~~, blockquotes, and horizontal rules
- Ordered, unordered, and nested lists
- Fenced code blocks with Pygments syntax highlighting (any theme)
- Tables with column alignment, emoji, and clickable links
- **Images** rendered inline — local files or remote URLs, and even raw `<img>`
  HTML tags; falls back to a text placeholder when not in a color terminal
- **Mermaid flowcharts** rendered as ASCII art locally — no data leaves your
  machine; falls back to the diagram source when the renderer isn't available
- Reads from a file or stdin; optional pager for long documents
- UTF-8 output on Windows out of the box, and clean handling of broken pipes

## Requirements

- **Python 3.13+** and [uv](https://docs.astral.sh/uv/)
- A terminal with **truecolor** support for images (Windows Terminal, iTerm2,
  and most modern terminals)
- *Optional:* [mermaid-ascii](https://github.com/AlexanderGrooff/mermaid-ascii)
  for Mermaid diagrams — install with Go:
  `go install github.com/AlexanderGrooff/mermaid-ascii@latest`

## Installation

Install it globally so `markterm` works from any directory:

```bash
git clone https://github.com/estevaofon/markterm.git
cd markterm
uv tool install .
```

This puts a `markterm` executable on your PATH (in `~/.local/bin`). If your shell
can't find it, run `uv tool update-shell` and reopen the terminal.

- Update after pulling changes: `uv tool install . --force`
- Uninstall: `uv tool uninstall markterm`
- Hacking on it? Install editable so changes take effect immediately:
  `uv tool install --editable . --force`

For one-off use without installing, run via `uv run markterm ...` after `uv sync`.

## Usage

```bash
# Render a file
markterm README.md

# Pipe Markdown in
cat notes.md | markterm

# Page a long document with a different code theme
markterm docs.md --pager --code-theme dracula

# Give wide diagrams more room
markterm architecture.md -w 120
```

### Options

| Flag                      | Description                                              |
| ------------------------- | -------------------------------------------------------- |
| `-t, --code-theme THEME`  | Pygments theme for fenced code blocks (default: monokai) |
| `--inline-code-theme THEME` | Pygments theme for `inline code`                       |
| `-w, --width N`           | Render width in columns (default: terminal width)        |
| `--image-width N`         | Max image width in columns (default: 50)                 |
| `--no-images`             | Show a text placeholder instead of drawing images        |
| `--no-mermaid`            | Show the diagram source instead of rendering Mermaid     |
| `-p, --pager`             | Page long output through a scrolling pager               |
| `--justify {default,left,center,right,full}` | Paragraph justification               |
| `--no-hyperlinks`         | Render links as plain text instead of clickable links    |
| `--no-color`              | Disable colors and styling                               |
| `--force-color`           | Force colored output even when piping to another program |

Try it on the included demos:

```bash
markterm sample.md   # local image (assets/owl.png) + a Mermaid flowchart
markterm noxy.md     # remote image via an <img> URL + a complex diagram
```

## How it works

- **Text** is rendered with Rich's Markdown engine (syntax highlighting via
  Pygments, tables, links, and more).
- **Images** are drawn with Unicode half-block characters (`▀`): each cell packs
  two vertical pixels (top = foreground, bottom = background), so any
  truecolor terminal can show pictures with no special image protocol.
  Transparent pixels let the terminal background show through. Sources can be a
  local path, an `http(s)` URL, or a raw HTML `<img>` tag.
- **Mermaid flowcharts** are rendered to ASCII art by
  [mermaid-ascii](https://github.com/AlexanderGrooff/mermaid-ascii), which runs
  locally and instantly. Since it doesn't support subgraphs or HTML labels,
  markterm first adapts the diagram (drops subgraph wrappers, strips HTML and
  emoji, flattens node shapes) so the flow still renders.

## Limitations

- Images need a truecolor terminal; in a plain pipe or with `--no-color` they
  show a text placeholder instead.
- Mermaid rendering needs `mermaid-ascii` on your PATH; without it (or for
  unsupported diagram types), markterm prints the diagram source. Subgraph
  grouping and HTML label formatting are not preserved.
- Wide diagrams are cropped (not reflowed) on narrow terminals — give them more
  width with `-w` or a wider window.

## Acknowledgements

Built on [Rich](https://github.com/Textualize/rich),
[Pillow](https://python-pillow.org/),
[markdown-it-py](https://github.com/executablebooks/markdown-it-py), and
[mermaid-ascii](https://github.com/AlexanderGrooff/mermaid-ascii).

## License

[MIT](LICENSE) © Estêvão Fonseca
