"""Generate a self-contained HTML page from a hardwaretest JSON system report."""

from __future__ import annotations

import json
import re
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any, Dict, List, Optional


# ── Section labels (German) ─────────────────────────────────────────────────
_SECTION_LABELS: Dict[str, tuple[str, str]] = {
    "fastfetch":    ("Systemübersicht",  "🖥️"),
    "lshw_cpu":     ("CPU",              "⚙️"),
    "lshw_memory":  ("Arbeitsspeicher",  "🧠"),
    "lshw_storage": ("Speicher-Controller", "💾"),
    "lshw_disk":    ("Festplatten",      "📀"),
    "lshw_raid":    ("RAID",             "🔗"),
    "lshw_scsi":    ("SCSI",             "🔌"),
    "lshw_display": ("Grafik",           "🖵"),
    "lshw_network": ("Netzwerk",         "🌐"),
    "lspci_vvv":    ("PCI-Geräte (lspci)", "📋"),
    "dmesg":        ("Kernel-Log (dmesg)", "📜"),
    "uname":        ("Kernel-Version",   "🐧"),
}


def _strip_ansi(text: str) -> str:
    return re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]").sub("", text)


def _parse_fastfetch(lines: List[str]) -> List[Dict[str, str]]:
    """Parse fastfetch key: value lines into structured data."""
    result = []
    for line in lines:
        line = _strip_ansi(line).strip()
        if not line or line.startswith("---") or line.startswith("\x1b"):
            continue
        if ":" in line:
            key, _, val = line.partition(":")
            result.append({"key": key.strip(), "value": val.strip()})
    return result


def _render_fastfetch_table(entries: List[Dict[str, str]]) -> str:
    rows = []
    for e in entries:
        k, v = escape(e["key"]), escape(e["value"])
        rows.append(f'<tr><td class="key">{k}</td><td>{v}</td></tr>')
    return f'<table class="info-table">{"".join(rows)}</table>'


def _render_pre_block(lines: List[str]) -> str:
    cleaned = "\n".join(
        _strip_ansi(line) for line in lines if not line.startswith("\x1b")
    )
    return f"<pre>{escape(cleaned)}</pre>"


def generate_html_report(json_path: Path) -> str:
    """Read a JSON system report and return a complete HTML string."""
    with open(json_path, "r", encoding="utf-8") as fh:
        data: Dict[str, Any] = json.load(fh)

    meta = data.get("meta", {})
    hostname = escape(meta.get("hostname", "Unbekannt"))
    generated = meta.get("generated_at", "")
    kernel = escape(meta.get("kernel", ""))

    # Parse timestamp
    try:
        dt = datetime.fromisoformat(generated.replace("Z", "+00:00"))
        generated_nice = dt.strftime("%d.%m.%Y  %H:%M:%S UTC")
    except Exception:
        generated_nice = escape(generated)

    commands: List[Dict[str, Any]] = data.get("commands", [])

    # ── Build navigation + sections ──────────────────────────────────────
    nav_items = []
    sections = []
    for cmd in commands:
        cid = cmd.get("id", "unknown")
        label, icon = _SECTION_LABELS.get(cid, (cid, "📄"))
        nav_items.append(f'<a href="#{cid}">{icon} {escape(label)}</a>')

        lines = cmd.get("output_lines", [])
        if not lines:
            raw = cmd.get("output", "")
            lines = raw.split("\n") if raw else ["(keine Ausgabe)"]

        # Special rendering for fastfetch
        if cid == "fastfetch":
            entries = _parse_fastfetch(lines)
            body = _render_fastfetch_table(entries) if entries else _render_pre_block(lines)
        else:
            body = _render_pre_block(lines)

        command_str = escape(cmd.get("command", ""))
        sections.append(
            f'<section id="{cid}">'
            f'<h2>{icon} {escape(label)}</h2>'
            f'<div class="cmd-hint">$ {command_str}</div>'
            f'{body}'
            f'</section>'
        )

    nav_html = "\n".join(nav_items)
    sections_html = "\n".join(sections)

    return f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Systemreport – {hostname}</title>
<style>
:root {{
    --bg: #1e1e2e;
    --surface: #282840;
    --surface2: #313150;
    --text: #cdd6f4;
    --text-dim: #9399b2;
    --accent: #89b4fa;
    --accent2: #74c7ec;
    --green: #a6e3a1;
    --border: #45475a;
}}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
html {{ scroll-behavior: smooth; }}
body {{
    font-family: 'Segoe UI', 'Ubuntu', 'Cantarell', sans-serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.6;
}}
.container {{
    display: grid;
    grid-template-columns: 220px 1fr;
    min-height: 100vh;
}}
/* ── Sidebar ──────────────────────────────────────────────────── */
nav {{
    position: sticky;
    top: 0;
    height: 100vh;
    overflow-y: auto;
    background: var(--surface);
    border-right: 1px solid var(--border);
    padding: 1rem 0;
    display: flex;
    flex-direction: column;
    gap: 2px;
}}
nav .brand {{
    text-align: center;
    padding: 0.5rem 1rem 1rem;
    font-weight: 700;
    font-size: 1rem;
    color: var(--accent);
    border-bottom: 1px solid var(--border);
    margin-bottom: 0.5rem;
}}
nav a {{
    display: block;
    padding: 0.45rem 1rem;
    color: var(--text-dim);
    text-decoration: none;
    font-size: 0.85rem;
    border-left: 3px solid transparent;
    transition: all 0.15s;
}}
nav a:hover, nav a:focus {{
    color: var(--text);
    background: var(--surface2);
    border-left-color: var(--accent);
}}
/* ── Main content ─────────────────────────────────────────────── */
main {{
    padding: 2rem 2.5rem;
    max-width: 960px;
}}
.header {{
    margin-bottom: 2rem;
    padding-bottom: 1rem;
    border-bottom: 1px solid var(--border);
}}
.header h1 {{
    font-size: 1.5rem;
    color: var(--accent);
    margin-bottom: 0.5rem;
}}
.header .meta {{
    display: flex;
    gap: 2rem;
    font-size: 0.85rem;
    color: var(--text-dim);
    flex-wrap: wrap;
}}
.header .meta span {{
    white-space: nowrap;
}}
section {{
    margin-bottom: 2rem;
    background: var(--surface);
    border-radius: 8px;
    border: 1px solid var(--border);
    overflow: hidden;
}}
section h2 {{
    padding: 0.7rem 1rem;
    font-size: 1rem;
    background: var(--surface2);
    border-bottom: 1px solid var(--border);
    color: var(--accent2);
}}
.cmd-hint {{
    padding: 0.35rem 1rem;
    font-family: 'JetBrains Mono', 'Fira Code', monospace;
    font-size: 0.75rem;
    color: var(--text-dim);
    background: rgba(0,0,0,0.15);
    border-bottom: 1px solid var(--border);
}}
pre {{
    padding: 1rem;
    font-family: 'JetBrains Mono', 'Fira Code', 'DejaVu Sans Mono', monospace;
    font-size: 0.82rem;
    line-height: 1.5;
    overflow-x: auto;
    white-space: pre-wrap;
    word-break: break-word;
    color: var(--text);
    max-height: 600px;
    overflow-y: auto;
}}
/* ── Info table (fastfetch) ───────────────────────────────────── */
.info-table {{
    width: 100%;
    border-collapse: collapse;
}}
.info-table tr:nth-child(even) {{
    background: rgba(255,255,255,0.02);
}}
.info-table td {{
    padding: 0.4rem 1rem;
    font-size: 0.88rem;
    border-bottom: 1px solid var(--border);
}}
.info-table td.key {{
    font-weight: 600;
    color: var(--accent);
    width: 180px;
    white-space: nowrap;
}}
/* ── Print ────────────────────────────────────────────────────── */
@media print {{
    .container {{ display: block; }}
    nav {{ display: none; }}
    main {{ max-width: 100%; padding: 1rem; }}
    section {{ break-inside: avoid; }}
    body {{ background: #fff; color: #222; }}
    section {{ border-color: #ccc; }}
    section h2 {{ background: #eee; color: #333; }}
    pre {{ font-size: 0.7rem; max-height: none; }}
}}
/* ── Responsive ───────────────────────────────────────────────── */
@media (max-width: 768px) {{
    .container {{ grid-template-columns: 1fr; }}
    nav {{
        position: relative;
        height: auto;
        flex-direction: row;
        flex-wrap: wrap;
        padding: 0.5rem;
    }}
    nav .brand {{ display: none; }}
    nav a {{ padding: 0.35rem 0.7rem; font-size: 0.8rem; border-left: none; }}
    main {{ padding: 1rem; }}
}}
</style>
</head>
<body>
<div class="container">
    <nav>
        <div class="brand">Hardwaretest Report</div>
        {nav_html}
    </nav>
    <main>
        <div class="header">
            <h1>Systemreport &ndash; {hostname}</h1>
            <div class="meta">
                <span>📅 {generated_nice}</span>
                <span>🐧 {kernel}</span>
            </div>
        </div>
        {sections_html}
    </main>
</div>
</body>
</html>"""


def json_to_html_report(json_path: Path, html_path: Optional[Path] = None) -> Path:
    """Convert a JSON system report to an HTML file.

    If *html_path* is ``None`` the HTML file is placed next to the JSON file
    with the same stem and ``.html`` extension.

    Returns the path of the generated HTML file.
    """
    if html_path is None:
        html_path = json_path.with_suffix(".html")
    html_path.write_text(generate_html_report(json_path), encoding="utf-8")
    return html_path
