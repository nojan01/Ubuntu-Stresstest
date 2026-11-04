"""Einheitliche HTML-Berichte für alle Test-Tabs.

Jeder Tab baut nach Testende ein :class:`Report` und schreibt es mit
:func:`write_report` nach ``~/Downloads/hwtest-<test>-JJJJMMTT-HHMMSS.html``.
"""

from __future__ import annotations

import platform
import re
import socket
from dataclasses import dataclass, field
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Optional, Sequence

from hardwaretest import __version__

STATUS_PASSED = "passed"
STATUS_FAILED = "failed"
STATUS_WARNING = "warning"
STATUS_CANCELLED = "cancelled"
STATUS_INFO = "info"

_STATUS_LABEL = {
    STATUS_PASSED: ("BESTANDEN", "PASSED"),
    STATUS_FAILED: ("FEHLGESCHLAGEN", "FAILED"),
    STATUS_WARNING: ("WARNUNG", "WARNING"),
    STATUS_CANCELLED: ("ABGEBROCHEN", "CANCELLED"),
    STATUS_INFO: ("INFORMATION", "INFORMATION"),
}
_STATUS_COLOR = {
    STATUS_PASSED: "#2e7d32",
    STATUS_FAILED: "#c62828",
    STATUS_WARNING: "#e65100",
    STATUS_CANCELLED: "#6d4c41",
    STATUS_INFO: "#1565c0",
}
_TEXT = {
    "Erstellt": "Created", "Rechner": "Host", "Kernel": "Kernel", "Programm": "Program",
    "Beginn": "Start", "Ende": "End", "Dauer": "Duration", "Ergebnis": "Result",
    "Parameter": "Parameters", "Hinweise": "Notes", "Rohausgabe": "Raw output",
    "Keine Daten": "No data",
}
_SLUG_UNSAFE = re.compile(r"[^a-z0-9]+")


def _t(key: str, language: str) -> str:
    return _TEXT.get(key, key) if language == "en" else key


def status_label(status: str, language: str = "de") -> str:
    pair = _STATUS_LABEL.get(status, (status.upper(), status.upper()))
    return pair[1] if language == "en" else pair[0]


def status_color(status: str) -> str:
    return _STATUS_COLOR.get(status, "#424242")


def slugify(test: str) -> str:
    """Dateinamen-tauglicher Testname: Kleinbuchstaben, Bindestriche."""
    text = test.lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    return _SLUG_UNSAFE.sub("-", text).strip("-") or "test"


def report_path(test: str, downloads: Optional[Path] = None, when: Optional[datetime] = None, suffix: str = ".html") -> Path:
    """``~/Downloads/hwtest-<test>-JJJJMMTT-HHMMSS.html`` (Ordner wird angelegt)."""
    base = Path(downloads) if downloads else Path.home() / "Downloads"
    base.mkdir(parents=True, exist_ok=True)
    stamp = (when or datetime.now()).strftime("%Y%m%d-%H%M%S")
    return base / f"hwtest-{slugify(test)}-{stamp}{suffix}"


@dataclass
class Section:
    """Ein Abschnitt des Berichts.

    ``kind``: ``table`` (rows = Liste von Zeilen, optional ``header``), ``pairs``
    (rows = Liste (Schlüssel, Wert)), ``list`` (rows = Texte), ``text`` (``text`` als
    ``<pre>``), ``html`` (``text`` wird unverändert eingefügt).
    """

    title: str
    kind: str = "pairs"
    rows: Sequence = field(default_factory=list)
    header: Sequence[str] = field(default_factory=list)
    text: str = ""
    collapsed: bool = False


@dataclass
class Report:
    test: str
    title: str
    status: str = STATUS_INFO
    summary: str = ""
    started: Optional[datetime] = None
    finished: Optional[datetime] = None
    parameters: list[tuple[str, str]] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)
    hints: list[str] = field(default_factory=list)
    raw_output: str = ""
    language: str = "de"

    def add_section(self, title: str, kind: str = "pairs", rows: Sequence = (), header: Sequence[str] = (),
                    text: str = "", collapsed: bool = False) -> Section:
        section = Section(title, kind, list(rows), list(header), text, collapsed)
        self.sections.append(section)
        return section


def _fmt_time(value: Optional[datetime], language: str) -> str:
    if value is None:
        return "–"
    return value.strftime("%Y-%m-%d %H:%M:%S" if language == "en" else "%d.%m.%Y %H:%M:%S")


def _fmt_duration(report: Report) -> str:
    if report.started is None or report.finished is None:
        return "–"
    seconds = max(0, int((report.finished - report.started).total_seconds()))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d} min"


def _cell(value: object) -> str:
    return escape("–" if value is None or value == "" else str(value))


def render_section(section: Section, language: str = "de") -> str:
    body = ""
    if section.kind == "pairs":
        rows = "".join(f"<tr><th>{_cell(k)}</th><td>{_cell(v)}</td></tr>" for k, v in section.rows)
        body = f'<table class="pairs">{rows}</table>' if rows else ""
    elif section.kind == "table":
        head = "".join(f"<th>{_cell(h)}</th>" for h in section.header)
        head = f"<thead><tr>{head}</tr></thead>" if head else ""
        rows = "".join("<tr>" + "".join(f"<td>{_cell(c)}</td>" for c in row) + "</tr>" for row in section.rows)
        body = f"<table>{head}<tbody>{rows}</tbody></table>" if rows else ""
    elif section.kind == "list":
        body = "<ul>" + "".join(f"<li>{_cell(r)}</li>" for r in section.rows) + "</ul>" if section.rows else ""
    elif section.kind == "text":
        body = f"<pre>{escape(section.text)}</pre>" if section.text.strip() else ""
    elif section.kind == "html":
        body = section.text
    if not body:
        body = f"<p class=\"dim\">{escape(_t('Keine Daten', language))}</p>"
    if section.collapsed:
        return f"<details><summary>{escape(section.title)}</summary>{body}</details>"
    return f"<h2>{escape(section.title)}</h2>{body}"


_STYLE = """<style>
:root{--text:#1d1f23;--dim:#5f6670;--line:#d7dbe0;--head:#eef1f4;--accent:#1f5fbf}
*{box-sizing:border-box}body{margin:0;color:var(--text);background:#fff;font:15px/1.5 Ubuntu,Arial,sans-serif}
main{max-width:1100px;margin:auto;padding:28px}header{border-left:8px solid var(--status);padding:14px 20px;background:var(--head);border-radius:8px}
h1{margin:0 0 6px;font-size:1.6em}h2{font-size:1.2em;margin:28px 0 8px;border-bottom:1px solid var(--line);padding-bottom:4px}
.verdict{display:inline-block;margin:8px 0;padding:4px 12px;border-radius:6px;color:#fff;background:var(--status);font-weight:bold;font-size:1.1em}
.summary{margin:6px 0 0;font-size:1.05em}.meta{margin-top:10px;color:var(--dim);font-size:.92em}.meta td{padding:1px 14px 1px 0}
table{border-collapse:collapse;margin:8px 0;max-width:100%}td,th{border:1px solid var(--line);padding:4px 9px;text-align:left;vertical-align:top}
thead th,table.pairs th{background:var(--head);font-weight:600}table.pairs th{width:32%}
pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f6f8;padding:10px;border-radius:6px;font-size:.9em}
details{margin:18px 0}summary{cursor:pointer;color:var(--accent);font-weight:600;font-size:1.1em}.dim{color:var(--dim)}
ul{margin:6px 0}footer{margin-top:36px;color:var(--dim);font-size:.85em;border-top:1px solid var(--line);padding-top:8px}
@media print{main{padding:0}details{display:block}details>summary{display:none}pre{font-size:.8em}}
</style>"""


def render_html(report: Report) -> str:
    """Eigenständige HTML-Seite im gemeinsamen Hardwaretest-Layout."""
    lang = report.language
    color = status_color(report.status)
    meta = [
        (_t("Rechner", lang), socket.gethostname()),
        (_t("Kernel", lang), platform.release()),
        (_t("Programm", lang), f"Hardwaretest {__version__}"),
        (_t("Erstellt", lang), _fmt_time(datetime.now(), lang)),
    ]
    if report.started or report.finished:
        meta += [(_t("Beginn", lang), _fmt_time(report.started, lang)),
                 (_t("Ende", lang), _fmt_time(report.finished, lang)),
                 (_t("Dauer", lang), _fmt_duration(report))]
    meta_html = "".join(f"<tr><td><b>{escape(k)}</b></td><td>{escape(v)}</td></tr>" for k, v in meta)
    parts = [
        f'<!doctype html><html lang="{lang}"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{escape(report.title)}</title>{_STYLE}</head>"
        f'<body style="--status:{color}"><main><header>',
        f"<h1>{escape(report.title)}</h1>",
        f'<div class="verdict">{escape(_t("Ergebnis", lang))}: {escape(status_label(report.status, lang))}</div>',
    ]
    if report.summary:
        parts.append(f'<p class="summary">{escape(report.summary)}</p>')
    parts.append(f'<table class="meta">{meta_html}</table></header>')
    if report.parameters:
        parts.append(render_section(Section(_t("Parameter", lang), "pairs", report.parameters), lang))
    for section in report.sections:
        parts.append(render_section(section, lang))
    if report.hints:
        parts.append(render_section(Section(_t("Hinweise", lang), "list", report.hints), lang))
    if report.raw_output.strip():
        parts.append(render_section(Section(_t("Rohausgabe", lang), "text", text=report.raw_output, collapsed=True), lang))
    parts.append(f"<footer>Hardwaretest {escape(__version__)} · hwtest-{escape(slugify(report.test))}</footer></main></body></html>")
    return "".join(parts)


def write_report(report: Report, downloads: Optional[Path] = None) -> Path:
    """Rendert den Bericht und schreibt ihn in den Downloads-Ordner; liefert den Pfad."""
    path = report_path(report.test, downloads, report.finished or datetime.now())
    path.write_text(render_html(report), encoding="utf-8")
    return path
