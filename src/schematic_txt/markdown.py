"""Renders ```` ```schematic ```` blocks in Markdown files to images.

GitHub (like most Markdown viewers) shows unknown code blocks as plain
text.  This module turns every block::

    ```schematic
    MCU.GND -> Sensor.GND
    ```

into a light and a dark SVG and puts a ``<picture>`` in front of the block,
which is folded into a ``<details>`` element::

    <!-- schematic.txt: ... -->
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="schematics/README-0123456789.dark.svg">
      <img alt="Schematic" src="schematics/README-0123456789.svg">
    </picture>

    <details>
    <summary>Schematic source</summary>

    ```schematic
    MCU.GND -> Sensor.GND
    ```

    </details>

The source block stays the single source of truth: running again finds the
generated wrapper and updates it in place.  Image names carry a hash of the
source, the layout effort and the output version, so unchanged blocks are
not laid out again and images that are no longer referenced are removed.

The image links are relative to the Markdown file by default.  Site
generators that serve a page from another place than its source (mdBook
renders the first chapter a second time as ``index.html``) need links that
work from anywhere: with a base URL, each link is that URL plus the path of
the image below the site's source root.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from schematic_txt.errors import ParseError, SchematicError
from schematic_txt.layout import config_for_effort, layout_schematic
from schematic_txt.parser import parse_schematic
from schematic_txt.svg import render_svg

LANGUAGE = "schematic"
DEFAULT_OUT_DIR = "schematics"
# Part of every image name.  Increase it whenever a release draws the same
# source differently, so that `--check` reports the old images as stale.
OUTPUT_VERSION = 3

MARKER = "<!-- schematic.txt: image rendered from the source below; edit the source, not the image -->"
# the generated wrapper, apart from the two image lines
_BETWEEN = ["</picture>", "", "<details>", "<summary>Schematic source</summary>", ""]
_AFTER = ["", "</details>"]
_HEAD_LEN = 4 + len(_BETWEEN)  # wrapper lines before the fence
_SOURCE_LINE = re.compile(r'^  <source media="\(prefers-color-scheme: dark\)" srcset="[^"]*">$')
_IMG_LINE = re.compile(r'^  <img alt="[^"]*" src="[^"]*">$')
_FENCE = re.compile(r"^(?P<fence>`{3,}|~{3,})[ \t]*(?P<info>[^`\s]*)[^`]*$")


@dataclass
class Block:
    """A ```` ```schematic ```` block, with its generated wrapper if present."""

    source: str
    start: int  # first line of the block (the wrapper's marker, if wrapped)
    end: int  # one past the last line (after ``</details>``, if wrapped)
    fence_line: int  # 0-based line of the opening fence
    close_line: int  # 0-based line of the closing fence
    fence: str  # the fence characters, reused when the block is rewritten


@dataclass
class Outcome:
    """What processing a Markdown file did (or, when checking, would do)."""

    path: Path
    blocks: int = 0
    rendered: list[Path] = field(default_factory=list)
    removed: list[Path] = field(default_factory=list)
    text_changed: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.rendered or self.removed or self.text_changed)


def find_blocks(text: str) -> list[Block]:
    """All top-level ```` ```schematic ```` blocks of a Markdown text.

    Blocks nested in another fence (e.g. a ```` ````markdown ```` example)
    are not taken: only fences at the start of a line are tracked, and
    everything inside a fence is skipped.
    """
    lines = text.split("\n")
    blocks: list[Block] = []
    i = 0
    while i < len(lines):
        m = _FENCE.match(lines[i])
        if not m:
            i += 1
            continue
        fence = m.group("fence")
        close = _closing_fence(lines, i + 1, fence)
        if m.group("info") == LANGUAGE and close is not None:
            start, end = i, close + 1
            if _has_wrapper(lines, start, end):
                start, end = start - _HEAD_LEN, end + len(_AFTER)
            source = "\n".join(lines[i + 1 : close])
            blocks.append(Block(source, start, end, i, close, fence))
        i = (close if close is not None else len(lines)) + 1
    return blocks


def _closing_fence(lines: list[str], start: int, fence: str) -> int | None:
    for j in range(start, len(lines)):
        stripped = lines[j].strip()
        if stripped and set(stripped) == {fence[0]} and len(stripped) >= len(fence):
            return j
    return None


def _has_wrapper(lines: list[str], start: int, end: int) -> bool:
    head = start - _HEAD_LEN
    if head < 0 or end + 1 >= len(lines):
        return False
    return (
        lines[head] == MARKER
        and lines[head + 1] == "<picture>"
        and _SOURCE_LINE.match(lines[head + 2]) is not None
        and _IMG_LINE.match(lines[head + 3]) is not None
        and lines[head + 4 : head + _HEAD_LEN] == _BETWEEN
        and lines[end : end + len(_AFTER)] == _AFTER
    )


def _wrapped(block: Block, light: str, dark: str) -> list[str]:
    return [
        MARKER,
        "<picture>",
        f'  <source media="(prefers-color-scheme: dark)" srcset="{dark}">',
        f'  <img alt="Schematic" src="{light}">',
        *_BETWEEN,
        block.fence + LANGUAGE,
        *([block.source] if block.source else []),
        block.fence,
        *_AFTER,
    ]


def _image_names(md_path: Path, block: Block, effort: str) -> tuple[str, str]:
    key = hashlib.sha256(f"{OUTPUT_VERSION}\n{effort}\n{block.source}".encode()).hexdigest()[:10]
    stem = f"{md_path.stem}-{key}"
    return f"{stem}.svg", f"{stem}.dark.svg"


def render_block(source: str, base_dir: Path, effort: str) -> tuple[str, str]:
    """Lays out a schematic once and draws it in both themes."""
    schematic = parse_schematic(source)
    layout_schematic(schematic, config_for_effort(effort))
    return (
        render_svg(schematic, theme="light", base_dir=base_dir),
        render_svg(schematic, theme="dark", base_dir=base_dir),
    )


def process_file(
    path: Path,
    *,
    out_dir: str = DEFAULT_OUT_DIR,
    effort: str = "balanced",
    force: bool = False,
    check: bool = False,
    base_url: str | None = None,
    site_root: Path | None = None,
) -> Outcome:
    """Renders the schematic blocks of one Markdown file and updates it.

    ``out_dir`` is relative to the Markdown file.  With ``check``, nothing
    is written; the outcome tells what would change.  A block that fails to
    render is left untouched and reported in ``Outcome.errors``.

    With ``base_url`` and ``site_root``, the images are linked as
    ``base_url`` plus their path below ``site_root`` instead of relative to
    the Markdown file.
    """
    outcome = Outcome(path)
    text = path.read_text(encoding="utf-8")
    blocks = find_blocks(text)
    outcome.blocks = len(blocks)
    lines = text.split("\n")
    images = path.parent / out_dir
    if base_url is None:
        prefix = out_dir.rstrip("/") + "/"
    else:
        try:
            prefix = _site_url(images, base_url, site_root or Path("."))
        except ValueError:
            outcome.errors.append(f"{path}: image directory {images} is not below the site root {site_root}")
            return outcome
    keep: set[str] = set()

    # back to front, so earlier line numbers stay valid while splicing
    for block in reversed(blocks):
        light, dark = _image_names(path, block, effort)
        keep.update((light, dark))
        missing = force or not (images / light).is_file() or not (images / dark).is_file()
        if missing:
            if check:
                outcome.rendered.append(images / light)
            else:
                try:
                    svgs = render_block(block.source, path.parent, effort)
                except SchematicError as exc:
                    outcome.errors.append(_locate(path, block, exc))
                    continue
                images.mkdir(parents=True, exist_ok=True)
                for name, svg in zip((light, dark), svgs, strict=True):
                    (images / name).write_text(svg, encoding="utf-8")
                outcome.rendered.append(images / light)
        lines[block.start : block.end] = _wrapped(block, prefix + light, prefix + dark)

    new_text = "\n".join(lines)
    if new_text != text:
        outcome.text_changed = True
        if not check:
            path.write_text(new_text, encoding="utf-8")

    if images.is_dir() and not outcome.errors:
        own = re.compile(rf"^{re.escape(path.stem)}-[0-9a-f]{{10}}(\.dark)?\.svg$")
        for image in sorted(images.iterdir()):
            if own.match(image.name) and image.name not in keep:
                outcome.removed.append(image)
                if not check:
                    image.unlink()
    return outcome


def _site_url(images: Path, base_url: str, site_root: Path) -> str:
    """The URL of the image directory, ending in a slash."""
    below = images.resolve().relative_to(site_root.resolve())
    url = base_url.rstrip("/") + "/"
    return url + "".join(f"{part}/" for part in below.parts)


def _locate(path: Path, block: Block, exc: SchematicError) -> str:
    if isinstance(exc, ParseError) and exc.line_num is not None:
        line = block.fence_line + 1 + exc.line_num
        return f"{path}:{line}: {exc.message}"
    return f"{path}:{block.fence_line + 1}: {exc}"


def markdown_files(paths: list[Path]) -> Iterator[Path]:
    """The given files, and every ``*.md`` below the given directories
    (skipping hidden directories)."""
    for path in paths:
        if path.is_dir():
            for md in sorted(path.rglob("*.md")):
                if not any(part.startswith(".") for part in md.relative_to(path).parts):
                    yield md
        else:
            yield path
