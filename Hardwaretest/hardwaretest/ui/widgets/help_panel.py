"""Hilfe-/Help-Tab mit zweisprachiger Dokumentation aller Tests und Optionen."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


# ---------------------------------------------------------------------------
# Hilfe-Texte
# ---------------------------------------------------------------------------

_HELP_DE = """\
<style>
  h1 { color: #88ccff; margin-top: 18px; }
  h2 { color: #77bbee; margin-top: 14px; border-bottom: 1px solid #555; padding-bottom: 3px; }
  h3 { color: #99ddaa; margin-top: 10px; }
  table { border-collapse: collapse; margin: 6px 0; }
  th { background: #333; padding: 4px 10px; text-align: left; }
  td { padding: 4px 10px; border-bottom: 1px solid #444; }
  code { background: #333; padding: 1px 4px; border-radius: 3px; }
  .warn { color: #ffaa00; }
  .good { color: #44ff44; }
  .bad  { color: #ff4444; }
</style>

<h1>🛠️ Hardwaretest – Hilfe</h1>

<h2>Übersicht</h2>
<p>
Diese Anwendung führt verschiedene Hardware-Stresstests durch, um die
Stabilität von CPU, RAM, Speichercontroller und Festplatten zu prüfen.
Alle Tests laufen unter Linux und nutzen bewährte Werkzeuge wie
<b>stress-ng</b>, <b>Prime95 (mprime)</b> und <b>fio</b>.
</p>

<h2>Allgemeine Bedienhinweise</h2>
<ul>
  <li><b>Dauer:</b> Stunden / Minuten / Sekunden – die Gesamtlaufzeit des Tests.</li>
  <li><b>CPU-Kerne / Threads:</b> Anzahl der Worker. Standardmäßig wird 1 Kern für
      die GUI freigehalten.</li>
  <li><b>Swap deaktivieren:</b> Für RAM-Tests unbedingt empfohlen, damit der
      physische Speicher getestet wird und nicht auf die Festplatte ausgewichen wird.</li>
  <li><b>Temperatur-Monitor:</b> Zeigt pro CPU-Socket eine Zusammenfassung
      (Min/Ø/Max). Bei Dual-Socket-Systemen mit vielen Cores wird die Anzeige
      automatisch kompakt. Klicken Sie auf „Alle Cores anzeigen" für Details.</li>
  <li><b>ECC/EDAC:</b> Zeigt korrigierbare und unkorrigierbare Speicherfehler an
      (nur bei ECC-RAM und geladenem EDAC-Kernelmodul).</li>
  <li><b>Pass/Fail:</b> Nach dem Test wird automatisch anhand der Ausgabe und des
      Exit-Codes bewertet, ob Fehler aufgetreten sind.</li>
</ul>

<hr>

<h2>Tab: stress-ng</h2>
<p>
Flexibler Stresstest für CPU und RAM mit verschiedenen Modi:
</p>
<table>
  <tr><th>Modus</th><th>Beschreibung</th><th>Einsatz</th></tr>
  <tr>
    <td><b>CPU-Last</b></td>
    <td>Maximale CPU-Belastung (Matrix, alle Methoden)</td>
    <td>CPU-Stabilität, Kühlung testen</td>
  </tr>
  <tr>
    <td><b>RAM-Test</b></td>
    <td>Aggressiver Speichertest mit <code>--verify</code></td>
    <td>RAM-Fehler finden</td>
  </tr>
  <tr>
    <td><b>RAM-Bandbreite</b></td>
    <td>memcpy + stream Workloads</td>
    <td>Speicherdurchsatz messen</td>
  </tr>
  <tr>
    <td><b>CPU + RAM kombiniert</b></td>
    <td>Gleichzeitig CPU + RAM mit Verifikation</td>
    <td>Gesamtsystem-Stabilität</td>
  </tr>
  <tr>
    <td><b>CPU-Cache-Stress</b></td>
    <td>Cache-Line-Bouncing zwischen Kernen (L3)</td>
    <td>Inter-Core-Kommunikation testen</td>
  </tr>
</table>
<p>
<b>RAM (MB):</b> Speichermenge pro Worker. Bei RAM-Tests sollte möglichst
der gesamte freie Speicher aufgeteilt werden.<br>
<b>memtest86+:</b> Für den gründlichsten RAM-Test empfehlen wir zusätzlich
memtest86+, das außerhalb des Betriebssystems läuft.
</p>

<hr>

<h2>Tab: Prime95</h2>
<p>
Nutzt <b>mprime</b> (die Linux-Version von Prime95) für Torture-Tests:
</p>
<table>
  <tr><th>Modus</th><th>Beschreibung</th></tr>
  <tr>
    <td><b>Blend</b></td>
    <td>Ausgewogener CPU+RAM-Test. Findet die meisten Fehler.
        <span class="warn">⚠ Swap vorher deaktivieren!</span></td>
  </tr>
  <tr>
    <td><b>Small FFTs</b></td>
    <td>Maximale CPU-Hitze, minimaler RAM-Verbrauch. Ideal zur
        Überprüfung der Kühlung und Spannungsversorgung.</td>
  </tr>
  <tr>
    <td><b>In-place large FFTs</b></td>
    <td>Belastet die Stromversorgung besonders stark.</td>
  </tr>
  <tr>
    <td><b>Custom</b></td>
    <td>Eigene RAM-Menge festlegen. Für gezielte Tests.
        <span class="warn">⚠ Swap vorher deaktivieren!</span></td>
  </tr>
</table>
<p>
<b>Binary-Pfad:</b> Standard ist <code>vendor/prime95/mprime</code> (mitgeliefert).
Fallback: <code>~/Prime95/mprime</code> oder <code>mprime</code> im PATH. Kann angepasst werden.<br>
<b>Minuten pro FFT:</b> Wie lange jeder FFT-Größenbereich getestet wird (Standard: 15 Min.).<br>
<b>Direktmodus:</b> Nutzt die bestehende <code>prime.txt</code>/<code>local.txt</code>
ohne sie zu überschreiben.
</p>

<hr>

<h2>Tab: Speichercontroller</h2>
<p>
Dieser Test zielt speziell auf den <b>Speichercontroller</b> (Memory Controller),
den <b>Speicherbus</b> und die <b>Cache-Kohärenz</b> ab – Bereiche, die von
normalen RAM-Tests oft nicht abgedeckt werden.
</p>
<p>
Im Gegensatz zum stress-ng RAM-Test (der Daten schreibt und zurückliest) erzeugt
dieser Tab gezielte Belastungsmuster, die den Speichercontroller und die
Kommunikation zwischen CPU-Kernen unter Druck setzen.
</p>

<h3>Verfügbare Stressoren</h3>
<table>
  <tr><th>Stressor</th><th>Was er tut</th><th>Findet…</th></tr>
  <tr>
    <td><b>cache</b></td>
    <td>Erzeugt massiven L1/L2/L3-Cache-Druck durch wiederholtes
        Lesen und Schreiben von Cache-Lines</td>
    <td>Cache-Defekte, Timing-Fehler im Cache-Subsystem</td>
  </tr>
  <tr>
    <td><b>membarrier</b></td>
    <td>Führt Memory-Barrier-Operationen aus, die die CPU zwingen,
        alle Speicher-Schreibvorgänge abzuschließen</td>
    <td>Fehler in der Speicher-Ordnungslogik (Memory Ordering)</td>
  </tr>
  <tr>
    <td><b>atomic</b></td>
    <td>Nutzt atomare Operationen (Compare-and-Swap, Fetch-and-Add etc.)
        auf gemeinsam genutzten Speicherbereichen</td>
    <td>Fehler bei gleichzeitigem Zugriff mehrerer Kerne auf denselben
        Speicher, Bus-Arbitrierungs-Probleme</td>
  </tr>
  <tr>
    <td><b>tlb-shootdown</b></td>
    <td>Erzwingt TLB-Invalidierungen (Translation Lookaside Buffer)
        zwischen CPU-Kernen</td>
    <td>Probleme bei der virtuellen Speicherverwaltung,
        TLB-Synchronisierungsfehler bei Multi-Core/Multi-Socket</td>
  </tr>
  <tr>
    <td><b>numa</b></td>
    <td>Greift gezielt auf Speicher anderer NUMA-Knoten zu
        (Cross-Socket-Zugriffe)</td>
    <td><span class="good">Besonders wichtig bei Dual-Socket-Systemen!</span>
        Findet Fehler im Inter-Socket-Speicherzugriff (QPI/UPI/Infinity Fabric)</td>
  </tr>
  <tr>
    <td><b>lockbus</b></td>
    <td>Sperrt den Speicherbus durch spezielle Locking-Operationen</td>
    <td>Bus-Lock-Probleme, Leistungseinbrüche durch übermäßige
        Bus-Sperrungen</td>
  </tr>
  <tr>
    <td><b>mcontend</b></td>
    <td>Erzeugt Speicher-Contention: Mehrere Kerne greifen gleichzeitig
        auf dieselben Cache-Lines zu (False Sharing)</td>
    <td>Stabilitätsprobleme bei hoher Cache-Contention,
        Speichercontroller-Überlastung</td>
  </tr>
</table>

<h3>Empfohlene Kombinationen</h3>
<ul>
  <li><b>Standard-Test:</b> cache + atomic + mcontend
      (gute Grundabdeckung)</li>
  <li><b>Dual-Socket-System:</b> cache + atomic + mcontend + <span class="good">numa</span> + tlb-shootdown
      (testet zusätzlich die Inter-Socket-Verbindung)</li>
  <li><b>Maximaler Stress:</b> Alle 7 Stressoren aktivieren
      (längere Laufzeit empfohlen, mind. 2 Stunden)</li>
</ul>

<h3>Optionen</h3>
<ul>
  <li><b>Threads:</b> Anzahl der Worker pro Stressor. Standardmäßig = CPU-Kerne − 1.</li>
  <li><b>Operationen:</b> Maximale Anzahl Durchläufe pro Stressor.
      „unbegrenzt" (0) = läuft bis die Zeit abläuft.</li>
  <li><b>Dauer:</b> Gesamtlaufzeit. Für gründliche Tests mind. 30 Min., besser 2+ Stunden.</li>
</ul>

<hr>

<h2>Tab: Disk (Datei / Geräte / Destruktiv)</h2>
<p>
Festplatten-/SSD-Tests mittels <b>fio</b> – optimiert für HPE ProLiant Server
mit SmartArray RAID-Controllern, SAS-/NVMe-Laufwerken.
</p>

<h3>Datei-Test (sicher)</h3>
<ul>
  <li>Liest/schreibt in einer temporären Datei auf einem beliebigen Dateisystem.</li>
  <li>Kein Root-Zugriff nötig – sicher auf Produktivsystemen.</li>
  <li>Workload wählbar: <code>read</code>, <code>write</code>, <code>randread</code>,
      <code>randwrite</code>, <code>readwrite</code>, <code>randrw</code>.</li>
  <li>Bei Schreib-Workloads wird automatisch <b>CRC32c-Verifikation</b> aktiviert
      (<code>--verify=crc32c --do_verify=1 --verify_fatal=1</code>).</li>
  <li>Geeignet für: Dateisystem-Integritätstests, SSD-Dauerlauf, IOPS-Benchmarks.</li>
</ul>

<h3>Geräte-Test (nur lesen)</h3>
<ul>
  <li>Liest alle Blöcke direkt vom Blockgerät via <code>pkexec/fio</code>.</li>
  <li>Erkennt defekte Sektoren, SCSI-Fehler und Medium-Errors – besonders wichtig
      nach RAID-Rebuilds auf HPE SmartArray.</li>
  <li>Nutzt <code>continue_on_error=read</code>: Liest alle Blöcke weiter, auch
      wenn einzelne fehlschlagen → komplettes Fehlerbild.</li>
  <li>Keine Datenänderung – sicher auf bestehenden Systemen.</li>
</ul>

<h3>Destruktiver Test <span class="bad">(⚠ DATENVERLUST!)</span></h3>
<ul>
  <li>Überschreibt <b>alle Daten</b> auf den gewählten Geräten und verifiziert
      anschließend per CRC32c-Rücklesen.</li>
  <li><code>verify_backlog=16384</code> / <code>verify_backlog_batch=4096</code> –
      optimiert für große RAID-Volumes (mehrere TB).</li>
  <li>Mehrere Durchläufe einstellbar (1–10) für intensiven Dauertest.</li>
  <li><span class="bad">Nur für leere/neue Datenträger verwenden!</span></li>
</ul>

<h3>I/O-Engine</h3>
<p>
Das Tool erkennt automatisch, ob <b>io_uring</b> verfügbar ist (Linux ≥ 5.1,
NVMe auf HPE Gen10+). Andernfalls wird <b>libaio</b> verwendet.
<code>io_uring</code> bietet deutlich niedrigere Latenz bei NVMe-Laufwerken.
</p>

<h3>SMART-Info &amp; HPE RAID-Info</h3>
<ul>
  <li><b>SMART-Info</b>: Zeigt Gesundheitsstatus, Temperatur, Betriebsstunden,
      reallocated/pending Sectors via <code>smartctl</code> an.</li>
  <li><b>HPE RAID-Info</b>: Zeigt SmartArray-Controller-Status und Konfiguration
      via <code>ssacli</code> / <code>hpssacli</code>.</li>
  <li>Buttons verfügbar in den Tabs „Geräte" und „Destruktiv".</li>
</ul>

<h3>Pass/Fail-Erkennung</h3>
<p>
Nach jedem Test wird automatisch bewertet:
<span class="good">✓ BESTANDEN</span> wenn keine Fehler erkannt wurden,
<span class="bad">✗ FEHLER</span> bei I/O-Fehlern, Verify-Fehlern,
SCSI-Errors oder HPE RAID-Fehlern.
</p>
<p>Erkannte Fehlermuster umfassen u.a.:</p>
<ul>
  <li>fio: <code>verify: bad header</code>, <code>verify failed</code>,
      <code>io_u error</code>, <code>short read</code></li>
  <li>Kernel: <code>I/O error</code>, <code>medium error</code>,
      <code>unrecovered read error</code></li>
  <li>SCSI/RAID: <code>sense key</code>, <code>SCSI error</code>,
      <code>drive fault</code>, <code>predictive failure</code></li>
</ul>

<hr>

<h2>Tab: Informationen</h2>
<p>
Zeigt Systeminformationen an: CPU, RAM, Mainboard, installierte Hardware.
Nutzt <code>lshw</code>, <code>fastfetch</code> und <code>lspci</code>.
</p>

<hr>

<h2>Tipps für gründliches Testen</h2>
<ol>
  <li>Zuerst <b>Swap deaktivieren</b>.</li>
  <li><b>stress-ng (RAM-Test)</b> für 2+ Stunden laufen lassen.</li>
  <li><b>Prime95 (Blend)</b> für 4+ Stunden – der Goldstandard.</li>
  <li><b>Speichercontroller-Test</b> mit allen Stressoren für 2+ Stunden
      (besonders bei Dual-Socket mit <code>numa</code>).</li>
  <li><b>Disk (Geräte)</b> – alle Laufwerke einmal komplett lesen
      (nach RAID-Rebuild besonders wichtig).</li>
  <li><b>Disk (Destruktiv)</b> – bei neuen Laufwerken mindestens 1 Durchlauf
      mit Schreib-/Lese-Verifikation.</li>
  <li><b>SMART-Info</b> vor und nach dem Test prüfen
      (Reallocated Sectors, Pending Sectors).</li>
  <li><b>HPE RAID-Info</b> auf Controller-/Drive-Warnungen prüfen.</li>
  <li><b>Kernel-Logs</b> und <b>MCE-Logs</b> regelmäßig auf Fehler prüfen.</li>
  <li>Bei ECC-RAM: <b>EDAC-Anzeige</b> auf Fehler beobachten.</li>
  <li>Optional: <b>memtest86+</b> über Nacht laufen lassen (außerhalb des OS).</li>
</ol>
"""

# -------------------------------------------------------------------------

_HELP_EN = """\
<style>
  h1 { color: #88ccff; margin-top: 18px; }
  h2 { color: #77bbee; margin-top: 14px; border-bottom: 1px solid #555; padding-bottom: 3px; }
  h3 { color: #99ddaa; margin-top: 10px; }
  table { border-collapse: collapse; margin: 6px 0; }
  th { background: #333; padding: 4px 10px; text-align: left; }
  td { padding: 4px 10px; border-bottom: 1px solid #444; }
  code { background: #333; padding: 1px 4px; border-radius: 3px; }
  .warn { color: #ffaa00; }
  .good { color: #44ff44; }
  .bad  { color: #ff4444; }
</style>

<h1>🛠️ Hardware Test – Help</h1>

<h2>Overview</h2>
<p>
This application runs various hardware stress tests to verify the stability
of CPU, RAM, memory controllers, and storage devices. All tests run on Linux
using proven tools such as <b>stress-ng</b>, <b>Prime95 (mprime)</b>, and <b>fio</b>.
</p>

<h2>General Usage Notes</h2>
<ul>
  <li><b>Duration:</b> Hours / Minutes / Seconds – total runtime of the test.</li>
  <li><b>CPU Cores / Threads:</b> Number of workers. By default, 1 core is
      reserved for the GUI.</li>
  <li><b>Disable Swap:</b> Strongly recommended for RAM tests, so that physical
      memory is tested rather than swapping to disk.</li>
  <li><b>Temperature Monitor:</b> Shows a per-socket summary (Min/Avg/Max). On
      dual-socket systems with many cores the display automatically becomes
      compact. Click "Show all cores" for details.</li>
  <li><b>ECC/EDAC:</b> Displays correctable and uncorrectable memory errors
      (only with ECC RAM and the EDAC kernel module loaded).</li>
  <li><b>Pass/Fail:</b> After the test, the result is automatically evaluated
      based on the output and exit code.</li>
</ul>

<hr>

<h2>Tab: stress-ng</h2>
<p>
Flexible stress test for CPU and RAM with multiple modes:
</p>
<table>
  <tr><th>Mode</th><th>Description</th><th>Use Case</th></tr>
  <tr>
    <td><b>CPU Load</b></td>
    <td>Maximum CPU utilization (matrix, all methods)</td>
    <td>Test CPU stability and cooling</td>
  </tr>
  <tr>
    <td><b>RAM Test</b></td>
    <td>Aggressive memory test with <code>--verify</code></td>
    <td>Find RAM errors</td>
  </tr>
  <tr>
    <td><b>RAM Bandwidth</b></td>
    <td>memcpy + stream workloads</td>
    <td>Measure memory throughput</td>
  </tr>
  <tr>
    <td><b>CPU + RAM Combined</b></td>
    <td>Simultaneous CPU + RAM with verification</td>
    <td>Overall system stability</td>
  </tr>
  <tr>
    <td><b>CPU Cache Stress</b></td>
    <td>Cache-line bouncing between cores (L3)</td>
    <td>Test inter-core communication</td>
  </tr>
</table>
<p>
<b>RAM (MB):</b> Memory per worker. For RAM tests, allocate as much free memory
as possible.<br>
<b>memtest86+:</b> For the most thorough RAM testing, we also recommend memtest86+,
which runs outside the operating system.
</p>

<hr>

<h2>Tab: Prime95</h2>
<p>
Uses <b>mprime</b> (the Linux version of Prime95) for torture tests:
</p>
<table>
  <tr><th>Mode</th><th>Description</th></tr>
  <tr>
    <td><b>Blend</b></td>
    <td>Balanced CPU+RAM test. Finds the most errors.
        <span class="warn">⚠ Disable swap first!</span></td>
  </tr>
  <tr>
    <td><b>Small FFTs</b></td>
    <td>Maximum CPU heat, minimal RAM usage. Ideal for verifying
        cooling and power delivery.</td>
  </tr>
  <tr>
    <td><b>In-place large FFTs</b></td>
    <td>Particularly stresses the power supply.</td>
  </tr>
  <tr>
    <td><b>Custom</b></td>
    <td>Set a custom RAM amount. For targeted tests.
        <span class="warn">⚠ Disable swap first!</span></td>
  </tr>
</table>
<p>
<b>Binary Path:</b> Default is <code>vendor/prime95/mprime</code> (bundled).
Fallback: <code>~/Prime95/mprime</code> or <code>mprime</code> in PATH. Can be customized.<br>
<b>Minutes per FFT:</b> How long each FFT size range is tested (default: 15 min).<br>
<b>Direct Mode:</b> Uses the existing <code>prime.txt</code>/<code>local.txt</code>
without overwriting them.
</p>

<hr>

<h2>Tab: Memory Controller</h2>
<p>
This test specifically targets the <b>memory controller</b>, <b>memory bus</b>,
and <b>cache coherency</b> – areas that normal RAM tests often do not cover.
</p>
<p>
Unlike the stress-ng RAM test (which writes and reads back data), this tab
generates specific load patterns that stress the memory controller and
inter-core communication.
</p>

<h3>Available Stressors</h3>
<table>
  <tr><th>Stressor</th><th>What It Does</th><th>Finds…</th></tr>
  <tr>
    <td><b>cache</b></td>
    <td>Creates massive L1/L2/L3 cache pressure through repeated
        reading and writing of cache lines</td>
    <td>Cache defects, timing errors in the cache subsystem</td>
  </tr>
  <tr>
    <td><b>membarrier</b></td>
    <td>Executes memory barrier operations that force the CPU to
        complete all pending memory writes</td>
    <td>Errors in memory ordering logic</td>
  </tr>
  <tr>
    <td><b>atomic</b></td>
    <td>Uses atomic operations (compare-and-swap, fetch-and-add, etc.)
        on shared memory regions</td>
    <td>Errors during concurrent access by multiple cores to the same
        memory, bus arbitration problems</td>
  </tr>
  <tr>
    <td><b>tlb-shootdown</b></td>
    <td>Forces TLB (Translation Lookaside Buffer) invalidations
        between CPU cores</td>
    <td>Virtual memory management issues,
        TLB synchronization errors on multi-core/multi-socket</td>
  </tr>
  <tr>
    <td><b>numa</b></td>
    <td>Deliberately accesses memory from other NUMA nodes
        (cross-socket access)</td>
    <td><span class="good">Especially important for dual-socket systems!</span>
        Finds errors in inter-socket memory access (QPI/UPI/Infinity Fabric)</td>
  </tr>
  <tr>
    <td><b>lockbus</b></td>
    <td>Locks the memory bus through special locking operations</td>
    <td>Bus lock issues, performance degradation from excessive
        bus locking</td>
  </tr>
  <tr>
    <td><b>mcontend</b></td>
    <td>Creates memory contention: multiple cores simultaneously
        access the same cache lines (false sharing)</td>
    <td>Stability problems under high cache contention,
        memory controller overload</td>
  </tr>
</table>

<h3>Recommended Combinations</h3>
<ul>
  <li><b>Standard test:</b> cache + atomic + mcontend
      (good baseline coverage)</li>
  <li><b>Dual-socket system:</b> cache + atomic + mcontend + <span class="good">numa</span> + tlb-shootdown
      (additionally tests the inter-socket interconnect)</li>
  <li><b>Maximum stress:</b> Enable all 7 stressors
      (longer runtime recommended, at least 2 hours)</li>
</ul>

<h3>Options</h3>
<ul>
  <li><b>Threads:</b> Number of workers per stressor. Default = CPU cores − 1.</li>
  <li><b>Operations:</b> Maximum number of iterations per stressor.
      "unlimited" (0) = runs until time expires.</li>
  <li><b>Duration:</b> Total runtime. For thorough tests at least 30 min, preferably 2+ hours.</li>
</ul>

<hr>

<h2>Tab: Disk (File / Device / Destructive)</h2>
<p>
Disk/SSD tests using <b>fio</b> – optimized for HPE ProLiant servers
with SmartArray RAID controllers, SAS/NVMe drives.
</p>

<h3>File Test (safe)</h3>
<ul>
  <li>Reads/writes to a temporary file on any filesystem.</li>
  <li>No root access needed – safe on production systems.</li>
  <li>Workload selectable: <code>read</code>, <code>write</code>, <code>randread</code>,
      <code>randwrite</code>, <code>readwrite</code>, <code>randrw</code>.</li>
  <li>Write workloads automatically enable <b>CRC32c verification</b>
      (<code>--verify=crc32c --do_verify=1 --verify_fatal=1</code>).</li>
  <li>Suitable for: filesystem integrity tests, SSD endurance, IOPS benchmarks.</li>
</ul>

<h3>Device Test (read-only)</h3>
<ul>
  <li>Reads all blocks directly from the block device via <code>pkexec/fio</code>.</li>
  <li>Detects bad sectors, SCSI errors and medium errors – especially important
      after RAID rebuilds on HPE SmartArray.</li>
  <li>Uses <code>continue_on_error=read</code>: continues reading all blocks even
      if some fail → complete error picture.</li>
  <li>No data modification – safe on existing systems.</li>
</ul>

<h3>Destructive Test <span class="bad">(⚠ DATA LOSS!)</span></h3>
<ul>
  <li>Overwrites <b>all data</b> on the selected devices and verifies
      by re-reading with CRC32c.</li>
  <li><code>verify_backlog=16384</code> / <code>verify_backlog_batch=4096</code> –
      optimized for large RAID volumes (multi-TB).</li>
  <li>Multiple passes configurable (1–10) for intensive endurance testing.</li>
  <li><span class="bad">Only use for empty/new drives!</span></li>
</ul>

<h3>I/O Engine</h3>
<p>
The tool automatically detects whether <b>io_uring</b> is available (Linux ≥ 5.1,
NVMe on HPE Gen10+). Otherwise <b>libaio</b> is used.
<code>io_uring</code> provides significantly lower latency on NVMe drives.
</p>

<h3>SMART Info &amp; HPE RAID Info</h3>
<ul>
  <li><b>SMART Info</b>: Shows health status, temperature, power-on hours,
      reallocated/pending sectors via <code>smartctl</code>.</li>
  <li><b>HPE RAID Info</b>: Shows SmartArray controller status and configuration
      via <code>ssacli</code> / <code>hpssacli</code>.</li>
  <li>Buttons available in the "Device" and "Destructive" tabs.</li>
</ul>

<h3>Pass/Fail Detection</h3>
<p>
After each test, the result is automatically evaluated:
<span class="good">✓ PASSED</span> if no errors were detected,
<span class="bad">✗ FAILED</span> on I/O errors, verify errors,
SCSI errors, or HPE RAID errors.
</p>
<p>Detected error patterns include:</p>
<ul>
  <li>fio: <code>verify: bad header</code>, <code>verify failed</code>,
      <code>io_u error</code>, <code>short read</code></li>
  <li>Kernel: <code>I/O error</code>, <code>medium error</code>,
      <code>unrecovered read error</code></li>
  <li>SCSI/RAID: <code>sense key</code>, <code>SCSI error</code>,
      <code>drive fault</code>, <code>predictive failure</code></li>
</ul>

<hr>

<h2>Tab: Information</h2>
<p>
Shows system information: CPU, RAM, mainboard, installed hardware.
Uses <code>lshw</code>, <code>fastfetch</code>, and <code>lspci</code>.
</p>

<hr>

<h2>Tips for Thorough Testing</h2>
<ol>
  <li>First, <b>disable swap</b>.</li>
  <li>Run <b>stress-ng (RAM test)</b> for 2+ hours.</li>
  <li>Run <b>Prime95 (Blend)</b> for 4+ hours – the gold standard.</li>
  <li>Run the <b>Memory Controller test</b> with all stressors for 2+ hours
      (especially on dual-socket with <code>numa</code>).</li>
  <li><b>Disk (Device)</b> – read all drives completely once
      (especially important after RAID rebuilds).</li>
  <li><b>Disk (Destructive)</b> – for new drives, at least 1 pass
      with write/read verification.</li>
  <li>Check <b>SMART Info</b> before and after testing
      (Reallocated Sectors, Pending Sectors).</li>
  <li>Check <b>HPE RAID Info</b> for controller/drive warnings.</li>
  <li>Regularly check <b>Kernel Logs</b> and <b>MCE Logs</b> for errors.</li>
  <li>With ECC RAM: monitor the <b>EDAC display</b> for errors.</li>
  <li>Optional: run <b>memtest86+</b> overnight (outside the OS).</li>
</ol>
"""


# ---------------------------------------------------------------------------
# Widget
# ---------------------------------------------------------------------------

class HelpPanel(QWidget):
    """Zweisprachiges Hilfe-Panel (Deutsch / English)."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)

        # Sprachwahl
        self._lang_box = QComboBox()
        self._lang_box.addItem("🇩🇪  Deutsch", userData="de")
        self._lang_box.addItem("🇬🇧  English", userData="en")
        self._lang_box.currentIndexChanged.connect(self._switch_language)

        lang_row = QHBoxLayout()
        lang_row.addWidget(QLabel("Sprache / Language:"))
        lang_row.addWidget(self._lang_box)
        lang_row.addStretch()

        # Scrollbarer Inhalt
        self._content = QLabel()
        self._content.setWordWrap(True)
        self._content.setTextFormat(Qt.TextFormat.RichText)
        self._content.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._content.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextBrowserInteraction
        )
        self._content.setOpenExternalLinks(True)
        self._content.setContentsMargins(12, 8, 12, 8)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._content)

        layout = QVBoxLayout()
        layout.addLayout(lang_row)
        layout.addWidget(scroll)
        self.setLayout(layout)

        # Standard: Deutsch
        self._switch_language()

    def _switch_language(self) -> None:
        lang = self._lang_box.currentData() or "de"
        if lang == "en":
            self._content.setText(_HELP_EN)
        else:
            self._content.setText(_HELP_DE)
