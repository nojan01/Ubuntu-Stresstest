"""GPU-Datenblatt (nvidia-smi) und HTML-Bericht für die NVIDIA-DCGM-Diagnose."""

from __future__ import annotations

import csv
import re
from datetime import datetime
from html import escape
from pathlib import Path
import subprocess
from typing import Callable, Optional

RunCommand = Callable[..., subprocess.CompletedProcess]

# (nvidia-smi-Feld, deutsche Bezeichnung, englische Bezeichnung)
SPEC_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("index", "Index", "Index"),
    ("name", "Modell", "Model"),
    ("serial", "Seriennummer", "Serial number"),
    ("uuid", "UUID", "UUID"),
    ("vbios_version", "VBIOS", "VBIOS"),
    ("pci.bus_id", "PCI-Bus", "PCI bus"),
    ("pci.device_id", "PCI-Geräte-ID", "PCI device ID"),
    ("pci.sub_device_id", "PCI-Subsystem-ID", "PCI subsystem ID"),
    ("driver_version", "Treiber", "Driver"),
    ("compute_cap", "Compute Capability", "Compute capability"),
    ("memory.total", "Speicher gesamt", "Memory total"),
    ("ecc.mode.current", "ECC aktuell", "ECC current"),
    ("ecc.mode.pending", "ECC nach Neustart", "ECC pending"),
    ("retired_pages.single_bit_ecc.count", "Retired Pages (SBE)", "Retired pages (SBE)"),
    ("retired_pages.double_bit_ecc.count", "Retired Pages (DBE)", "Retired pages (DBE)"),
    ("retired_pages.pending", "Retired Pages ausstehend", "Retired pages pending"),
    ("pcie.link.gen.max", "PCIe-Generation max.", "PCIe generation max."),
    ("pcie.link.gen.current", "PCIe-Generation aktuell", "PCIe generation current"),
    ("pcie.link.width.max", "PCIe-Breite max.", "PCIe width max."),
    ("pcie.link.width.current", "PCIe-Breite aktuell", "PCIe width current"),
    ("power.limit", "Leistungsgrenze", "Power limit"),
    ("power.default_limit", "Leistungsgrenze Standard", "Power limit default"),
    ("power.max_limit", "Leistungsgrenze max.", "Power limit max."),
    ("clocks.max.graphics", "Takt Grafik max.", "Graphics clock max."),
    ("clocks.max.sm", "Takt SM max.", "SM clock max."),
    ("clocks.max.memory", "Takt Speicher max.", "Memory clock max."),
    ("temperature.gpu", "Temperatur", "Temperature"),
    ("fan.speed", "Lüfter", "Fan"),
    ("inforom.img", "InfoROM Image", "InfoROM image"),
    ("inforom.ecc", "InfoROM ECC", "InfoROM ECC"),
    ("inforom.pwr", "InfoROM Power", "InfoROM power"),
    ("persistence_mode", "Persistence Mode", "Persistence mode"),
    ("compute_mode", "Compute Mode", "Compute mode"),
)

REMAPPED_FIELDS = ("gpu_bus_id", "remapped_rows.correctable", "remapped_rows.uncorrectable",
                   "remapped_rows.pending", "remapped_rows.failure")
REMAPPED_LABELS = {
    "remapped_rows.correctable": ("Remapped Rows (korrigierbar)", "Remapped rows (correctable)"),
    "remapped_rows.uncorrectable": ("Remapped Rows (unkorrigierbar)", "Remapped rows (uncorrectable)"),
    "remapped_rows.pending": ("Remapped Rows ausstehend", "Remapped rows pending"),
    "remapped_rows.failure": ("Remapping fehlgeschlagen", "Remapping failure"),
}

GpuSpecs = dict[str, str]


def parse_csv_rows(text: str) -> list[list[str]]:
    return [row for row in csv.reader(text.splitlines(), skipinitialspace=True) if row]


def _clean(value: str) -> str:
    text = value.strip()
    return "–" if not text or text.casefold() in {"n/a", "[n/a]", "[not supported]", "not supported", "n/a (n/a)"} else text


def parse_specs_csv(text: str, fields: tuple[str, ...] = tuple(f for f, _d, _e in SPEC_FIELDS)) -> list[GpuSpecs]:
    specs: list[GpuSpecs] = []
    for row in parse_csv_rows(text):
        if len(row) < 2:
            continue
        specs.append({name: _clean(row[i]) if i < len(row) else "–" for i, name in enumerate(fields)})
    return specs


def merge_remapped_rows(specs: list[GpuSpecs], text: str) -> None:
    """Remapped-Rows-Zeilen (Ampere+) den GPUs über die PCI-Bus-ID zuordnen."""
    by_bus = {s.get("pci.bus_id", "").casefold(): s for s in specs}
    for row in parse_csv_rows(text):
        if len(row) < len(REMAPPED_FIELDS):
            continue
        target = by_bus.get(row[0].strip().casefold())
        if target is None:
            continue
        for name, value in zip(REMAPPED_FIELDS[1:], row[1:]):
            target[name] = _clean(value)


_INVALID_FIELD = re.compile(r'Field "([^"]+)" is not a valid field', re.I)


def query_gpu_fields(fields: list[str], run: RunCommand = subprocess.run,
                     errors: Optional[list[str]] = None) -> tuple[list[str], str]:
    """``nvidia-smi --query-gpu`` mit Rückzug: Felder, die der Treiber nicht kennt, werden
    anhand der Fehlermeldung entfernt und die Abfrage wiederholt. Liefert (Felder, CSV)."""
    remaining = list(fields)
    for _attempt in range(len(fields)):
        if not remaining:
            break
        try:
            completed = run(["nvidia-smi", f"--query-gpu={','.join(remaining)}", "--format=csv,noheader"],
                            check=False, capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError) as exc:
            if errors is not None:
                errors.append(f"nvidia-smi: {exc}")
            return [], ""
        if completed.returncode == 0:
            return remaining, completed.stdout
        message = (completed.stderr or completed.stdout or "").strip()
        bad = _INVALID_FIELD.findall(message)
        bad = [b for b in bad if b in remaining]
        if not bad:
            if errors is not None:
                errors.append(f"nvidia-smi (exit {completed.returncode}): {message[:300] or 'keine Ausgabe'}")
            return [], ""
        if errors is not None:
            errors.append("nvidia-smi kennt nicht: " + ", ".join(bad))
        remaining = [f for f in remaining if f not in bad]
    return [], ""


def collect_gpu_specs(indices: Optional[list[int]] = None, run: RunCommand = subprocess.run,
                      errors: Optional[list[str]] = None) -> list[GpuSpecs]:
    """Datenblatt aller (oder der gewählten) GPUs über nvidia-smi; leere Liste ohne Treiber.
    ``errors`` sammelt Hinweise, warum Felder oder das ganze Datenblatt fehlen."""
    fields = [f for f, _d, _e in SPEC_FIELDS]
    used, stdout = query_gpu_fields(fields, run, errors)
    if not used:
        return []
    specs = parse_specs_csv(stdout, tuple(used))
    for spec in specs:
        for name in fields:
            spec.setdefault(name, "–")
    try:
        remapped = run(["nvidia-smi", f"--query-remapped-rows={','.join(REMAPPED_FIELDS)}", "--format=csv,noheader"],
                       check=False, capture_output=True, text=True, timeout=30)
        if remapped.returncode == 0:
            merge_remapped_rows(specs, remapped.stdout)
    except (OSError, subprocess.SubprocessError):
        pass
    if indices is not None:
        wanted = {str(i) for i in indices}
        specs = [s for s in specs if s.get("index") in wanted]
    return specs


def _spec_rows(language: str) -> list[tuple[str, str]]:
    de = language != "en"
    rows = [(field, d if de else e) for field, d, e in SPEC_FIELDS if field != "index"]
    rows.extend((field, labels[0] if de else labels[1]) for field, labels in REMAPPED_LABELS.items())
    return rows


_STATUS_COLOR = {"pass": "#2e7d32", "fail": "#c62828", "warn": "#e65100", "skip": "#757575"}
_WORDS = {
    "de": {"pass": "bestanden", "fail": "FEHLGESCHLAGEN", "warn": "Warnung", "skip": "übersprungen"},
    "en": {"pass": "passed", "fail": "FAILED", "warn": "warning", "skip": "skipped"},
}


def specs_table_html(specs: list[GpuSpecs], language: str = "de") -> str:
    de = language != "en"
    if not specs:
        return "<p>" + escape("Keine GPU-Daten verfügbar (nvidia-smi)." if de else "No GPU data available (nvidia-smi).") + "</p>"
    head = "<tr><th></th>" + "".join(f"<th>GPU {escape(s.get('index', '?'))}</th>" for s in specs) + "</tr>"
    body = ""
    for field, label in _spec_rows(language):
        values = [s.get(field, "–") for s in specs]
        if all(v == "–" for v in values):
            continue
        body += f"<tr><th>{escape(label)}</th>" + "".join(f"<td>{escape(v)}</td>" for v in values) + "</tr>"
    return f"<table>{head}{body}</table>"


def results_table_html(payload: object, language: str = "de") -> str:
    """Ergebnistabelle Kategorie / Test / GPU / Status / Meldungen aus dem dcgmi-JSON."""
    from hardwaretest.tests.nvidia_runner import _entity_label, _find_categories, _lookup, _messages, _status_key

    de = language != "en"
    words = _WORDS["de" if de else "en"]
    _root, categories = _find_categories(payload)
    rows = ""
    for category in categories:
        if not isinstance(category, dict):
            continue
        cat_name = str(category.get("category") or category.get("name") or "?")
        for test in category.get("tests") or []:
            if not isinstance(test, dict):
                continue
            name = str(test.get("name", "?"))
            results = test.get("results")
            summary = test.get("test_summary") if isinstance(test.get("test_summary"), dict) else {}
            if results in (None, "", []):
                results = [{"status": summary.get("status", test.get("status", "?"))}]
            if not isinstance(results, list):
                results = [results]
            for result in results:
                if not isinstance(result, dict):
                    continue
                key = _status_key(result.get("status"))
                status = words[key] if key else str(result.get("status", "?"))
                color = _STATUS_COLOR.get(key or "", "")
                messages = []
                messages += [("Warnung: " if de else "Warning: ") + t for t in _messages(result.get("warnings"))]
                messages += [("Fehler: " if de else "Error: ") + t for t in _messages(result.get("errors"))]
                messages += ["Info: " + t for t in _messages(result.get("info"))]
                rows += (f"<tr><td>{escape(cat_name)}</td><td>{escape(name)}</td><td>{escape(_entity_label(result) or '–')}</td>"
                         f'<td style="color:{color};font-weight:bold">{escape(status)}</td>'
                         f"<td>{'<br>'.join(escape(m) for m in messages)}</td></tr>")
            summary_key = _status_key(summary.get("status")) if summary else None
            summary_msgs = [t for k in ("errors", "warnings") for t in _messages(summary.get(k))] if summary else []
            if summary and test.get("results") not in (None, "", []) and (summary_key == "fail" or summary_msgs):
                color = _STATUS_COLOR.get(summary_key or "", "")
                rows += (f"<tr><td>{escape(cat_name)}</td><td>{escape(name)}</td><td>{'Gesamt' if de else 'overall'}</td>"
                         f'<td style="color:{color};font-weight:bold">{escape(words.get(summary_key, str(summary.get("status", "?"))))}</td>'
                         f"<td>{'<br>'.join(escape(m) for m in summary_msgs)}</td></tr>")
    runtime_error = _lookup(payload, ("runtime_error",))
    extra = ""
    if isinstance(runtime_error, str) and runtime_error.strip():
        extra = f'<p style="color:#c62828"><b>{"DCGM-Fehler" if de else "DCGM error"}:</b> {escape(" ".join(runtime_error.split()))}</p>'
    if not rows:
        return extra or "<p>" + escape("Keine Testergebnisse im DCGM-Bericht." if de else "No test results in the DCGM report.") + "</p>"
    head = ("<tr><th>Kategorie</th><th>Test</th><th>GPU</th><th>Status</th><th>Meldungen</th></tr>" if de
            else "<tr><th>Category</th><th>Test</th><th>GPU</th><th>Status</th><th>Messages</th></tr>")
    return extra + f"<table>{head}{rows}</table>"


def render_html(specs: list[GpuSpecs], language: str = "de", *, payload: object = None, title_suffix: str = "",
                verdict: str = "", verdict_color: str = "", details: Optional[list[str]] = None,
                hints: Optional[list[str]] = None, meta: Optional[list[tuple[str, str]]] = None) -> str:
    """Eigenständige HTML-Seite: Kopf, Datenblatt, (optional) DCGM-Ergebnisse und Hinweise."""
    de = language != "en"
    title = ("NVIDIA-GPU-Bericht" if de else "NVIDIA GPU report") + (f" – {title_suffix}" if title_suffix else "")
    style = ("<style>body{font-family:sans-serif;margin:24px}table{border-collapse:collapse;margin:8px 0}"
             "td,th{border:1px solid #ccc;padding:3px 8px;text-align:left;vertical-align:top}th{background:#f0f0f0}"
             "h1{font-size:1.5em}h2{font-size:1.2em;margin-top:28px}.meta td{border:none;padding:1px 12px 1px 0}"
             "pre{background:#f7f7f7;padding:8px;overflow:auto}</style>")
    meta_rows = [("Erstellt" if de else "Created", datetime.now().strftime("%d.%m.%Y %H:%M:%S" if de else "%Y-%m-%d %H:%M:%S"))]
    meta_rows += meta or []
    html = [f'<!doctype html><html lang="{"de" if de else "en"}"><head><meta charset="utf-8"><title>{escape(title)}</title>{style}</head><body>',
            f"<h1>{escape(title)}</h1>",
            '<table class="meta">' + "".join(f"<tr><td><b>{escape(k)}</b></td><td>{escape(v)}</td></tr>" for k, v in meta_rows) + "</table>"]
    if verdict:
        html.append(f'<p style="font-size:1.3em;font-weight:bold;color:{verdict_color or "#000"}">{escape(verdict)}</p>')
    if details:
        html.append("<p>" + "<br>".join(escape(d) for d in details) + "</p>")
    html.append(f"<h2>{'Technische Daten' if de else 'Technical data'}</h2>" + specs_table_html(specs, language))
    if payload is not None:
        html.append(f"<h2>{'DCGM-Ergebnisse' if de else 'DCGM results'}</h2>" + results_table_html(payload, language))
    if hints:
        html.append(f"<h2>{'Hinweise' if de else 'Notes'}</h2><ul>" + "".join(f"<li>{escape(h)}</li>" for h in hints) + "</ul>")
    html.append("</body></html>")
    return "".join(html)


def report_path(kind: str = "dcgm-diagnose", downloads: Optional[Path] = None) -> Path:
    base = downloads or (Path.home() / "Downloads")
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{kind}-{datetime.now():%Y%m%d-%H%M%S}.html"
