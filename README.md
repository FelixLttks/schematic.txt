# schematic.txt

Write down how a circuit is wired, as plain text, and get a schematic you
can actually read. Parts are placed and wires routed automatically, with
the layout tuned for following wires by eye: few crossings, straight
connections, buses drawn as one trunk.

It is meant for documenting the wiring of hobby and embedded projects next
to the code, in Markdown, under version control. It is not a PCB or
simulation tool.

<!-- schematic.txt: image rendered from the source below; edit the source, not the image -->
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="schematics/README-1f98f04a3c.dark.svg">
  <img alt="Schematic" src="schematics/README-1f98f04a3c.svg">
</picture>

<details>
<summary>Schematic source</summary>

```schematic
MCU: ESP32-C3
  pins: 3V3, GND, GPIO8(SDA), GPIO9(SCL), GPIO2

Sensor: BME280
OLED: SSD1306 Display
R1: resistor 330
D1: LED green

MCU.3V3 -> Sensor.VIN, OLED.VCC : 3V3
MCU.GND -> Sensor.GND, OLED.GND, D1.K : GND
MCU.GPIO8 -> Sensor.SDA, OLED.SDA : I2C_SDA
MCU.GPIO9 -> Sensor.SCL, OLED.SCL : I2C_SCL
MCU.GPIO2 -> R1.1
R1.2 -> D1.A
```

</details>

## Install

Python 3.10 or newer, no other dependencies. Run it without installing:

```sh
uvx --from git+https://github.com/FelixLttks/schematic.txt schematic --help
```

or install the `schematic` command with `pipx install
git+https://github.com/FelixLttks/schematic.txt` (or `pip install` in a
virtual environment).

## Schematics in Markdown

Put the description in a fenced block with the language `schematic`:

````markdown
```schematic
MCU.GND -> Sensor.GND
MCU.3V3 -> Sensor.VCC
```
````

GitHub shows unknown code blocks as plain text, so the images are rendered
ahead of time. `schematic md` turns each block into an SVG (one for light,
one for dark mode) in a `schematics/` folder next to the Markdown file,
and inserts the image above the block. The source stays in the file,
folded away under the image, and remains the thing you edit. The block in
this README went through exactly that.

```sh
schematic md README.md docs/   # files, or every *.md below a directory
schematic md --check .         # exit 1 if any image is missing or stale
```

Running it again only renders blocks that changed, and deletes images that
are no longer used.

### On GitHub

This repository is also a GitHub Action. To render schematics on every push
and commit the images:

```yaml
# .github/workflows/schematics.yml
name: Schematics
on:
  push:
    paths: ["**.md"]
permissions:
  contents: write
jobs:
  render:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
      - uses: FelixLttks/schematic.txt@main
      - name: Commit images
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add -A
          git diff --cached --quiet || { git commit -m "docs: render schematics" && git push; }
```

Inputs: `paths` (default `.`), `effort`, `out-dir` (default `schematics`)
and `check` (`"true"` to only verify, e.g. in pull requests).

To render before committing instead, there is a [pre-commit](https://pre-commit.com)
hook:

```yaml
repos:
  - repo: https://github.com/FelixLttks/schematic.txt
    rev: main
    hooks:
      - id: schematic-md
```

## Live editor

```sh
schematic serve README.md
```

opens an editor with a live preview in the browser. Given a Markdown file,
it lists its schematic blocks; given a `.txt` file, it edits the whole
file. Changes in the browser are saved to the file, and changes made in
your own editor show up in the preview. Without a file it is a scratch pad.

The editor also has a part builder for [image parts](docs/syntax.md#image-parts):
upload a picture of a board and click on the grid to place its pins.

`schematic md --watch README.md` is the alternative if you prefer your
editor's Markdown preview: it re-renders the images on every save.

## Command line

```sh
schematic render board.txt                  # writes board.svg
schematic render board.txt -o - --theme dark --report
```

`--report` prints quality figures for the layout (crossings, bends, wire
length, a readability score). Every command takes `--effort`:

| Effort | Use | Time for ~20 parts |
|---|---|---|
| `fast` | live preview | seconds |
| `balanced` | default | about 2 min |
| `thorough` | dense circuits, final images | several minutes |

Small circuits take a few seconds at any level. Layout is deterministic:
the same input gives the same image.

From Python:

```python
from schematic_txt import render

svg = render("MCU.GND -> Sensor.GND", theme="dark", effort="fast")
```

## Syntax in short

```text
# Declaring parts is optional: parts used in connections exist automatically.
MCU: ESP32-C3
  pins: 3V3, GND, GPIO8(SDA)

# Resistors, capacitors, LEDs, diodes, fuses and switches are drawn as symbols.
R1: resistor 4.7k

# One pin to many, as a named net.
MCU.GPIO8 -> Sensor.SDA, R1.1 : I2C_SDA
MCU.3V3 -> R1.2
```

The full reference, including pin sides, YAML input and parts drawn from
images, is in [docs/syntax.md](docs/syntax.md). More inputs are in
[examples/](examples/).

## Development

```sh
uv sync                      # virtual environment with the dev tools
uv run pytest -m "not slow"  # quick tests; plain `uv run pytest` runs everything
uv run ruff check && uv run ruff format --check
```

The full test run lays out every example and test case. Layout runs use
several processes, so run one at a time on a small machine.

[docs/layout-engine.md](docs/layout-engine.md) explains how placement and
routing work and why. Before changing the engine, compare against the last
commit with

```sh
uv run scripts/layout_report.py --baseline HEAD
```

which renders all examples and test cases with both versions and writes a
report with metrics and images to `build/layout-report/`.
