from datetime import datetime
from pathlib import Path

from hardwaretest.core import report as rep


def test_report_path_uses_hwtest_prefix_and_timestamp(tmp_path):
    when = datetime(2026, 11, 4, 17, 5, 9)
    path = rep.report_path("Disk (Geräte)", tmp_path, when)
    assert path == tmp_path / "hwtest-disk-geraete-20261104-170509.html"
    assert tmp_path.is_dir()


def test_slugify():
    assert rep.slugify("RAM-Fülltest") == "ram-fuelltest"
    assert rep.slugify("  NVIDIA GPU / DCGM ") == "nvidia-gpu-dcgm"
    assert rep.slugify("***") == "test"


def test_render_html_contains_all_sections():
    r = rep.Report(test="stress-ng", title="stress-ng – Test", status=rep.STATUS_FAILED, summary="2 Fehler",
                   started=datetime(2026, 1, 1, 10, 0, 0), finished=datetime(2026, 1, 1, 11, 2, 3),
                   parameters=[("Dauer", "120 s")], hints=["Hinweis <x>"], raw_output="zeile 1\nzeile 2")
    r.add_section("Tabelle", "table", [["a", 1], ["b", None]], header=["Name", "Wert"])
    r.add_section("Liste", "list", ["eins", "zwei"])
    r.add_section("Leer", "pairs", [])
    html = rep.render_html(r)
    assert "<title>stress-ng – Test</title>" in html
    assert "FEHLGESCHLAGEN" in html and rep.status_color(rep.STATUS_FAILED) in html
    assert "1:02:03" in html
    assert "<th>Dauer</th><td>120 s</td>" in html
    assert "<th>Name</th><th>Wert</th>" in html and "<td>a</td><td>1</td>" in html and "<td>–</td>" in html
    assert "Hinweis &lt;x&gt;" in html
    assert "<details><summary>Rohausgabe</summary><pre>zeile 1\nzeile 2</pre></details>" in html
    assert "Keine Daten" in html


def test_render_html_english_labels():
    r = rep.Report(test="x", title="X", status=rep.STATUS_PASSED, language="en")
    html = rep.render_html(r)
    assert "Result: PASSED" in html and "Host" in html and "Created" in html


def test_write_report(tmp_path):
    r = rep.Report(test="NVMe", title="NVMe", status=rep.STATUS_WARNING, finished=datetime(2026, 2, 3, 4, 5, 6))
    path = rep.write_report(r, tmp_path)
    assert path.name == "hwtest-nvme-20260203-040506.html"
    assert "WARNUNG" in path.read_text(encoding="utf-8")
