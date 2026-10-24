"""Markdown integration: finding blocks, rewriting files, image bookkeeping."""

import pytest

from schematic_txt import markdown
from schematic_txt.markdown import MARKER, find_blocks, markdown_files, process_file

DOC = """\
# Wiring

```schematic
MCU.GND -> Sensor.GND
MCU.3V3 -> Sensor.VCC
```

Text after.
"""


@pytest.fixture
def doc(tmp_path):
    path = tmp_path / "README.md"
    path.write_text(DOC, encoding="utf-8")
    return path


def _images(path):
    out = path.parent / "schematics"
    return sorted(p.name for p in out.iterdir()) if out.is_dir() else []


def test_finds_top_level_blocks_only():
    text = "````markdown\n```schematic\nA.1 -> B.1\n```\n````\n\n```schematic\nC.1 -> D.1\n```\n"
    blocks = find_blocks(text)
    assert [b.source for b in blocks] == ["C.1 -> D.1"]


def test_other_languages_and_unclosed_fences_are_ignored():
    assert find_blocks("```python\nA.1 -> B.1\n```\n") == []
    assert find_blocks("```schematic\nA.1 -> B.1\n") == []


def test_renders_and_wraps_block(doc):
    outcome = process_file(doc, effort="fast")
    assert outcome.errors == []
    assert outcome.blocks == 1
    text = doc.read_text()
    assert text.startswith("# Wiring\n\n" + MARKER + "\n<picture>\n")
    assert "<summary>Schematic source</summary>\n\n```schematic\nMCU.GND -> Sensor.GND" in text
    assert text.endswith("```\n\n</details>\n\nText after.\n")
    dark, light = _images(doc)  # "x.dark.svg" sorts before "x.svg"
    assert dark == light.replace(".svg", ".dark.svg")
    assert f'src="schematics/{light}"' in text
    assert f'srcset="schematics/{dark}"' in text


def test_second_run_changes_nothing(doc):
    process_file(doc, effort="fast")
    before = doc.read_text()
    outcome = process_file(doc, effort="fast")
    assert not outcome.changed
    assert doc.read_text() == before
    assert len(find_blocks(before)) == 1


def test_edited_block_replaces_its_images(doc):
    process_file(doc, effort="fast")
    old = _images(doc)
    doc.write_text(doc.read_text().replace("Sensor.VCC", "Sensor.VIN"))
    outcome = process_file(doc, effort="fast")
    assert len(outcome.rendered) == 1
    assert len(outcome.removed) == 2
    assert set(_images(doc)).isdisjoint(old)


def test_check_reports_without_writing(doc):
    outcome = process_file(doc, effort="fast", check=True)
    assert outcome.changed
    assert doc.read_text() == DOC
    assert _images(doc) == []
    process_file(doc, effort="fast")
    assert not process_file(doc, effort="fast", check=True).changed


def test_effort_is_part_of_the_image_name(doc):
    process_file(doc, effort="fast")
    assert process_file(doc, effort="balanced", check=True).changed


def test_broken_block_is_reported_with_its_file_line(doc):
    doc.write_text(DOC + "\n```schematic\nMCU.X ->\n```\n")
    outcome = process_file(doc, effort="fast")
    assert len(outcome.errors) == 1
    # the broken connection is on line 11 of the file
    assert outcome.errors[0].startswith(f"{doc}:11: ")
    assert "```schematic\nMCU.X ->\n```" in doc.read_text()


def test_markdown_files_walks_directories(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / ".hidden").mkdir()
    for name in ("a.md", "docs/b.md", ".hidden/c.md", "notes.txt"):
        (tmp_path / name).write_text("")
    found = [p.relative_to(tmp_path).as_posix() for p in markdown_files([tmp_path])]
    assert found == ["a.md", "docs/b.md"]


def test_base_url_links_images_below_the_site_root(tmp_path):
    chapter = tmp_path / "src" / "user" / "wiring.md"
    chapter.parent.mkdir(parents=True)
    chapter.write_text(DOC, encoding="utf-8")
    outcome = process_file(chapter, effort="fast", base_url="/project/", site_root=tmp_path / "src")
    assert outcome.errors == []
    text = chapter.read_text()
    assert 'src="/project/user/schematics/wiring-' in text
    assert 'srcset="/project/user/schematics/wiring-' in text
    assert not process_file(chapter, effort="fast", base_url="/project/", site_root=tmp_path / "src").changed


def test_switching_to_a_base_url_rewrites_the_links_only(doc):
    process_file(doc, effort="fast")
    images = _images(doc)
    outcome = process_file(doc, effort="fast", base_url="https://example.com/docs", site_root=doc.parent)
    assert outcome.text_changed and not outcome.rendered and not outcome.removed
    assert f'src="https://example.com/docs/schematics/{images[1]}"' in doc.read_text()


def test_image_directory_outside_the_site_root_is_an_error(doc, tmp_path):
    outcome = process_file(doc, effort="fast", base_url="/", site_root=tmp_path / "elsewhere")
    assert outcome.errors and "not below the site root" in outcome.errors[0]
    assert doc.read_text() == DOC


def test_output_version_is_part_of_the_image_name(doc, monkeypatch):
    process_file(doc, effort="fast")
    monkeypatch.setattr(markdown, "OUTPUT_VERSION", markdown.OUTPUT_VERSION + 1)
    assert process_file(doc, effort="fast", check=True).changed
