"""Command-line interface."""

import json

import pytest

from schematic_txt import __version__
from schematic_txt.cli import main


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "board.txt"
    path.write_text("MCU.GND -> Sensor.GND\nMCU.3V3 -> Sensor.VCC\n", encoding="utf-8")
    return path


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_render_writes_svg_next_to_input(source):
    assert main(["render", str(source), "--effort", "fast"]) == 0
    assert source.with_suffix(".svg").read_text().startswith("<svg")


def test_render_to_stdout_with_reports(source, tmp_path, capsys):
    report = tmp_path / "report.json"
    code = main(
        ["render", str(source), "-o", "-", "--effort", "fast", "--report", "--json-report", str(report)]
    )
    assert code == 0
    out, err = capsys.readouterr()
    assert out.startswith("<svg")
    assert "Readability score" in err
    assert json.loads(report.read_text())["violation_count"] == 0


def test_render_reports_parse_errors(tmp_path, capsys):
    bad = tmp_path / "bad.txt"
    bad.write_text("MCU.GND ->\n")
    assert main(["render", str(bad)]) == 1
    assert "Line 1" in capsys.readouterr().err


def test_render_missing_file(tmp_path, capsys):
    assert main(["render", str(tmp_path / "nope.txt")]) == 1
    assert "cannot read" in capsys.readouterr().err
