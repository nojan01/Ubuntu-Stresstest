"""Small, application-wide runtime translation service.

The application deliberately keeps its translations in Python rather than
requiring Qt Linguist at run time.  This makes the standalone Debian package
and the source checkout behave identically.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, QSettings, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractButton,
    QComboBox,
    QGroupBox,
    QLabel,
    QLineEdit,
    QMenu,
    QSpinBox,
    QTabWidget,
    QWidget,
)


# The source language of the UI is German.  Keeping the keys as the actual
# source text also means that an untranslated new label stays readable.
_EN: dict[str, str] = {
    "Individuelle Temperaturgrenzen …": "Individual temperature limits …",
    "Partitionen": "Partitions",
    "Dateisystem": "Filesystem",
    "Langzeitmonitoring": "Long-term monitoring",
    "Begleitendes Monitoring mit Sicherheitsabbruch": "Background monitoring with safety stop",
    "Temperaturgrenze / Messintervall": "Temperature limit / sample interval",
    "Aktion läuft": "Operation running",
    "Bitte das Monitoring zuerst stoppen und laufende Dateisystem-Aktionen abschließen lassen.": "Please stop monitoring first and let filesystem operations finish.",
    "Bitte laufende Tests beenden und warten, bis die Hintergrundaktion abgeschlossen ist.": "Please stop running tests and wait for background operations to finish.",
    "HPE Smart Array – RAID-Info (nur HPE-Controller)": "HPE Smart Array – RAID info (HPE controllers only)",
    "Modell: {value}": "Model: {value}",
    "Seriennummer: {value}": "Serial number: {value}",
    "Firmware: {value}": "Firmware: {value}",
    "NVMe-Selbsttest bestanden.": "NVMe self-test passed.",
    "✓ NVMe-Selbsttest BESTANDEN": "✓ NVMe self-test PASSED",
    "NVMe-Selbsttest fehlgeschlagen.": "NVMe self-test failed.",
    "✗ NVMe-Selbsttest FEHLGESCHLAGEN (Code {code})": "✗ NVMe self-test FAILED (code {code})",
    "NVMe-Selbsttest abgeschlossen. Das Laufwerk liefert kein auswertbares Ergebnisprotokoll.": "NVMe self-test completed. The drive does not provide an interpretable result log.",
    "? NVMe-Selbsttest abgeschlossen – Ergebnis nicht verfügbar": "? NVMe self-test completed – result unavailable",
    "Selbsttest-Protokoll lesen": "Read self-test log",
    "NVMe-Selbsttest läuft": "NVMe self-test is running",
    "Während eines NVMe-Selbsttests kann kein zusätzlicher Lese-Test gestartet werden.": "No additional read test can be started while an NVMe self-test is running.",
    "Sequenzielles Lesen": "Sequential read",
    "Zufälliges Lesen": "Random read",
    "Benchmark-Modus": "Benchmark mode",
    "Benchmark-Dauer": "Benchmark duration",
    "Lese-Benchmark starten": "Start read benchmark",
    "Vollständigen Lese-Test starten": "Start full read test",
    "Lese-Test stoppen": "Stop read test",
    "NVMe-Lesetests (nicht destruktiv)": "NVMe read tests (non-destructive)",
    "Der Benchmark und der vollständige Lese-Test schreiben keine Daten. Der vollständige Test liest jeden Block und kann viele Stunden dauern.": "The benchmark and full read test do not write data. The full test reads every block and can take many hours.",
    "Vollständigen NVMe-Lese-Test starten": "Start full NVMe read test",
    "Der vollständige Lese-Test liest jeden Block von {device}. Er schreibt keine Daten, kann aber mehrere Stunden dauern. Fortfahren?": "The full read test reads every block on {device}. It does not write data, but can take several hours. Continue?",
    "Der vollständige Lese-Test liest jeden Block der ausgewählten Laufwerke ({count}): {devices}. Er schreibt keine Daten, kann aber mehrere Stunden dauern. Fortfahren?": "The full read test reads every block on the selected drives ({count}): {devices}. It does not write data, but can take several hours. Continue?",
    "Werkzeug nicht installiert": "Tool not installed",
    "Für NVMe-Lesetests wird das Paket fio benötigt.": "NVMe read tests require the fio package.",
    "NVMe-Lese-Benchmark läuft …": "NVMe read benchmark running …",
    "Vollständiger NVMe-Lese-Test läuft – alle Blöcke werden gelesen …": "Full NVMe read test running – reading every block …",
    "⌛ NVMe-Lese-Test läuft": "⌛ NVMe read test is running",
    "✗ NVMe-Lese-Test konnte nicht gestartet werden": "✗ NVMe read test could not be started",
    "? NVMe-Lese-Test abgebrochen": "? NVMe read test cancelled",
    "Lese-Test wurde manuell beendet.": "Read test was stopped manually.",
    "✗ NVMe-Lese-Test ohne Ergebnis beendet": "✗ NVMe read test ended without a result",
    "✗ NVMe-Lese-Test FEHLGESCHLAGEN": "✗ NVMe read test FAILED",
    "Unbekannter Lese-Fehler.": "Unknown read error.",
    "✓ NVMe-Lese-Benchmark BESTANDEN": "✓ NVMe read benchmark PASSED",
    "✓ NVMe-Lese-Benchmark BESTANDEN – {throughput:.1f} MiB/s | {iops:.0f} IOPS | Ø-Latenz: {latency:.3f} ms": "✓ NVMe read benchmark PASSED – {throughput:.1f} MiB/s | {iops:.0f} IOPS | avg. latency: {latency:.3f} ms",
    "✓ Vollständiger NVMe-Lese-Test BESTANDEN": "✓ Full NVMe read test PASSED",
    "Keine Lese-Fehler erkannt.": "No read errors detected.",
    "Keine Lese-Fehler auf {count} Laufwerk(en) erkannt.": "No read errors detected on {count} drive(s).",
    "NVMe": "NVMe",
    "NVIDIA GPU": "NVIDIA GPU",
    "nvidia-smi liefert Inventar- und Gesundheitsdaten. NVIDIA DCGM führt optional aktive Bereitschafts-, Speicher-, PCIe- und Belastungsdiagnosen aus.": "nvidia-smi provides inventory and health data. NVIDIA DCGM optionally runs active readiness, memory, PCIe and stress diagnostics.",
    "NVIDIA-GPUs suchen": "Scan NVIDIA GPUs",
    "Linux-Basisprüfung": "Linux basic check",
    "Installationsanleitung: /opt/hardwaretest/docs/NVIDIA_GPU.md": "Installation guide: /opt/hardwaretest/docs/NVIDIA_GPU.md",
    "Alle NVIDIA-GPUs auswählen": "Select all NVIDIA GPUs",
    "NVIDIA-GPUs werden gesucht …": "Scanning NVIDIA GPUs …",
    "GPU-Auswahl": "GPU selection",
    "NVIDIA-DCGM-Diagnose": "NVIDIA DCGM diagnostics",
    "DCGM-Diagnosestufe": "DCGM diagnostic level",
    "Stufe 1 – Schnelltest / Einsatzbereitschaft": "Level 1 – Quick / readiness",
    "Stufe 2 – Mittel / PCIe und Speicher": "Level 2 – Medium / PCIe and memory",
    "Stufe 3 – Lange Hardwarediagnose": "Level 3 – Long hardware diagnostics",
    "Stufe 4 – Erweiterte Langzeitdiagnose": "Level 4 – Extended long diagnostics",
    "DCGM-Diagnose starten": "Start DCGM diagnostics",
    "DCGM-Diagnose stoppen": "Stop DCGM diagnostics",
    "nvidia-smi wurde nicht gefunden. Bitte zuerst einen NVIDIA-Treiber installieren.": "nvidia-smi was not found. Install an NVIDIA driver first.",
    "Linux-Basisprüfung: Keine NVIDIA-PCIe-GPU erkannt.": "Linux basic check: No NVIDIA PCIe GPU detected.",
    "Nur die Linux-Basisprüfung ist verfügbar.": "Only the Linux basic check is available.",
    "✓ NVIDIA-Treiberinstallation BESTANDEN": "✓ NVIDIA driver installation PASSED",
    "NVIDIA-Treiber und nvidia-smi funktionieren.": "NVIDIA driver and nvidia-smi are working.",
    "✗ NVIDIA-Treiber fehlt oder funktioniert nicht korrekt": "✗ NVIDIA driver is missing or not working correctly",
    "Aktive NVIDIA-Tests sind deaktiviert. Die Linux-Basisprüfung zeigt die Ursache.": "Active NVIDIA tests are disabled. The Linux basic check shows the cause.",
    "Linux-Basisprüfung (nur lesend)": "Linux basic check (read-only)",
    "NVIDIA-PCIe-Geräte: {value}": "NVIDIA PCIe devices: {value}",
    "Gebundene Treiber: {value}": "Bound drivers: {value}",
    "Kernelmodule: {value}": "Kernel modules: {value}",
    "Geräteknoten: {value}": "Device nodes: {value}",
    "nvidia-smi: {value}": "nvidia-smi: {value}",
    "Fehlermeldung: {value}": "Error message: {value}",
    "nicht installiert": "not installed",
    "FEHLER": "ERROR",
    "Keine NVIDIA-GPUs gefunden.": "No NVIDIA GPUs found.",
    "{count} NVIDIA-GPU(s) gefunden.": "{count} NVIDIA GPU(s) found.",
    "DCGM ist nicht installiert. Inventar und Gesundheitsdaten sind verfügbar; aktive Diagnosen benötigen dcgmi.": "DCGM is not installed. Inventory and health data are available; active diagnostics require dcgmi.",
    "NVIDIA-GPU-Erkennung fehlgeschlagen.": "NVIDIA GPU detection failed.",
    "Keine NVIDIA-GPU ausgewählt": "No NVIDIA GPU selected",
    "Bitte mindestens eine NVIDIA-GPU auswählen.": "Please select at least one NVIDIA GPU.",
    "DCGM nicht installiert": "DCGM not installed",
    "Für aktive NVIDIA-Diagnosen wird dcgmi benötigt.": "Active NVIDIA diagnostics require dcgmi.",
    "DCGM-Stufe {level} auf {count} GPU(s) starten? Laufende GPU-Arbeiten sollten vorher beendet werden.": "Start DCGM level {level} on {count} GPU(s)? Running GPU workloads should be stopped first.",
    "DCGM-Diagnose fehlgeschlagen": "DCGM diagnostics failed",
    "⌛ DCGM-Diagnose läuft": "⌛ DCGM diagnostics running",
    "DCGM-Stufe {level} läuft auf {count} GPU(s) …": "DCGM level {level} is running on {count} GPU(s) …",
    "? DCGM-Diagnose abgebrochen": "? DCGM diagnostics cancelled",
    "DCGM-Diagnose wurde manuell beendet.": "DCGM diagnostics were stopped manually.",
    "✓ DCGM-Diagnose BESTANDEN": "✓ DCGM diagnostics PASSED",
    "Ergebnisse: {passed} bestanden, {warned} Warnungen, {skipped} übersprungen.": "Results: {passed} passed, {warned} warnings, {skipped} skipped.",
    "✗ DCGM-Diagnose FEHLGESCHLAGEN": "✗ DCGM diagnostics FAILED",
    "DCGM meldet {failed} fehlgeschlagene Prüfung(en). Details stehen im Protokoll.": "DCGM reports {failed} failed check(s). See the log for details.",
    "Treiber: {driver} | CUDA: {cuda}": "Driver: {driver} | CUDA: {cuda}",
    "VRAM: {used}/{total} MiB": "VRAM: {used}/{total} MiB",
    "Temperatur: {value} °C": "Temperature: {value} °C",
    "Leistung: {draw}/{limit} W": "Power: {draw}/{limit} W",
    "ECC: {mode} | korrigiert: {corrected} | unkorrigiert: {uncorrected}": "ECC: {mode} | corrected: {corrected} | uncorrected: {uncorrected}",
    "Auslastung: {value} %": "Utilization: {value} %",
    "Takte: Grafik {graphics} MHz | SM {sm} MHz | Speicher {memory} MHz": "Clocks: graphics {graphics} MHz | SM {sm} MHz | memory {memory} MHz",
    "PCIe: Gen {generation}/{max_generation} | Breite x{width}/x{max_width}": "PCIe: Gen {generation}/{max_generation} | width x{width}/x{max_width}",
    "GPU-Liveüberwachung": "GPU live monitoring",
    "Messintervall": "Sampling interval",
    "Liveüberwachung starten": "Start live monitoring",
    "Liveüberwachung stoppen": "Stop live monitoring",
    "Liveüberwachung ist gestoppt.": "Live monitoring is stopped.",
    "GPU-Liveüberwachung läuft …": "GPU live monitoring running …",
    "GPU-Liveüberwachung läuft – {count} GPU(s)": "GPU live monitoring running – {count} GPU(s)",
    "Keine Messwerte für die ausgewählten GPUs verfügbar.": "No telemetry is available for the selected GPUs.",
    "GPU-Liveüberwachung fehlgeschlagen.": "GPU live monitoring failed.",
    "GPU {index}: Last {util}% | {temperature} °C | VRAM {used}/{total} MiB | {power} W | Grafik/SM/Speicher {graphics}/{sm}/{memory} MHz | PCIe Gen {generation} x{width}": "GPU {index}: load {util}% | {temperature} °C | VRAM {used}/{total} MiB | {power} W | graphics/SM/memory {graphics}/{sm}/{memory} MHz | PCIe Gen {generation} x{width}",
    "NVMe-Laufwerk": "NVMe drive",
    "NVMe-Laufwerk für Diagnose/Selbsttest": "NVMe drive for diagnostics/self-test",
    "NVMe-Ziellaufwerk": "NVMe target drive",
    "NVMe-Ziellaufwerke": "NVMe target drives",
    "Alle NVMe-Laufwerke auswählen": "Select all NVMe drives",
    "Kein NVMe-Laufwerk ausgewählt": "No NVMe drive selected",
    "Bitte mindestens ein NVMe-Ziellaufwerk auswählen.": "Please select at least one NVMe target drive.",
    "NVMe-Laufwerke suchen": "Scan NVMe drives",
    "SMART-/Gesundheitsdaten lesen": "Read SMART / health data",
    "Selbsttest": "Self-test",
    "Kurzer NVMe-Selbsttest": "Short NVMe self-test",
    "Erweiterter NVMe-Selbsttest": "Extended NVMe self-test",
    "NVMe-Selbsttest starten": "Start NVMe self-test",
    "Die Diagnose liest Controller- und SMART-Daten. NVMe-Selbsttests sind nicht destruktiv; sie können jedoch je nach Laufwerk einige Zeit dauern.": "Diagnostics read controller and SMART data. NVMe self-tests are non-destructive, but can take some time depending on the drive.",
    "nvme-cli ist nicht installiert. Bitte das Paket nvme-cli installieren.": "nvme-cli is not installed. Please install the nvme-cli package.",
    "{count} NVMe-Laufwerk(e) gefunden.": "{count} NVMe drive(s) found.",
    "Keine NVMe-Laufwerke gefunden.": "No NVMe drives found.",
    "NVMe-Gesundheitsdaten werden gelesen …": "Reading NVMe health data …",
    "NVMe-Gesundheitsdaten aktualisiert.": "NVMe health data updated.",
    "NVMe-Selbsttest-Protokoll": "NVMe self-test log",
    "NVMe-Selbsttest-Protokoll wird gelesen …": "Reading NVMe self-test log …",
    "⌛ NVMe-Selbsttest-Protokoll wird gelesen …": "⌛ Reading NVMe self-test log …",
    "Gerät: {device}": "Device: {device}",
    "Gelesen: {timestamp}": "Read: {timestamp}",
    "Aktueller Selbsttest: läuft": "Current self-test: running",
    "Aktueller Selbsttest: keiner": "Current self-test: none",
    "Fortschritt: vom Laufwerk nicht gemeldet": "Progress: not reported by the drive",
    "Fortschritt: {progress}%": "Progress: {progress}%",
    "Letztes Ergebnis: BESTANDEN (Code 0)": "Latest result: PASSED (code 0)",
    "Letztes Ergebnis: FEHLGESCHLAGEN (Code {code})": "Latest result: FAILED (code {code})",
    "Letztes Ergebnis: nicht vorhanden oder nicht auswertbar": "Latest result: unavailable or not readable",
    "Vollständige Protokolldaten (nvme-cli):": "Complete log data (nvme-cli):",
    "Keine Protokolldaten geliefert.": "No log data returned.",
    "NVMe-Selbsttest wird gestartet …": "Starting NVMe self-test …",
    "NVMe-Aktion fehlgeschlagen.": "NVMe action failed.",
    "NVMe-Aktion fehlgeschlagen": "NVMe action failed",
    "Soll der {test} NVMe-Selbsttest auf {device} gestartet werden? Der Test ist nicht destruktiv.": "Start the {test} NVMe self-test on {device}? The test is non-destructive.",
    "kurzen": "short",
    "erweiterten": "extended",
    "NVMe-Selbsttest wurde gestartet. Den Fortschritt bitte später mit „SMART-/Gesundheitsdaten lesen“ prüfen.": "NVMe self-test started. Check its progress later with “Read SMART / health data”.",
    "NVMe-Selbsttest läuft – {progress}. Start weiterer Selbsttests ist gesperrt.": "NVMe self-test is running – {progress}. Starting another self-test is disabled.",
    "Fortschritt wird vom Laufwerk nicht gemeldet": "The drive does not report progress",
    "NVMe-Selbsttest abgeschlossen. Bitte Gesundheitsdaten erneut lesen.": "NVMe self-test completed. Please read the health data again.",
    "✓ NVMe-Selbsttest abgeschlossen – bitte Gesundheitsdaten zur Ergebnisprüfung erneut lesen.": "✓ NVMe self-test completed – please read the health data again to verify the result.",
    "⌛ NVMe-Selbsttest läuft": "⌛ NVMe self-test is running",
    "NVMe-Selbsttest läuft. Der Fortschritt kann von diesem Laufwerk derzeit nicht abgefragt werden.": "NVMe self-test is running. This drive does not currently provide readable progress.",
    "NVMe-Selbsttest läuft – Fortschritt wird vom Laufwerk nicht gemeldet. Start weiterer Selbsttests ist gesperrt.": "NVMe self-test is running – the drive does not report progress. Starting another self-test is disabled.",
    "NVMe-Selbsttest läuft – Fortschritt: {progress}%. Start weiterer Selbsttests ist gesperrt.": "NVMe self-test is running – progress: {progress}%. Starting another self-test is disabled.",
    "✗ NVMe-Aktion fehlgeschlagen": "✗ NVMe action failed",
    "NVMe-Gerät: {device}": "NVMe device: {device}",
    "Status: {status}": "Status: {status}",
    "Kritische Warnungen: {value}": "Critical warnings: {value}",
    "Temperatur: {value}": "Temperature: {value}",
    "Verfügbarer Reserveplatz: {value}": "Available spare: {value}",
    "Verschleiß: {value}": "Wear: {value}",
    "Betriebsstunden: {value}": "Power-on hours: {value}",
    "Unsichere Abschaltungen: {value}": "Unsafe shutdowns: {value}",
    "Medienfehler: {value}": "Media errors: {value}",
    "Fehlerlog-Einträge: {value}": "Error log entries: {value}",
    "Gelesene Dateneinheiten: {value}": "Data units read: {value}",
    "Geschriebene Dateneinheiten: {value}": "Data units written: {value}",
    "Keine": "None",
    "OK": "OK",
    "WARNUNG": "WARNING",
    "Netzwerk": "Network",
    "Gesamttest": "Test plan",
    "– Gesamttest ÜBERSPRUNGEN – keine Prüfung ausgeführt": "– Test plan SKIPPED – no checks executed",
    "Vollständiger NVMe-Lesetest (optional)": "Full NVMe read test (optional)",
    "NVMe-Auswahl aktualisieren": "Refresh NVMe selection",
    "Alle auswählen": "Select all",
    "Auswahl gilt für SMART und Lesetest; ohne Auswahl prüft SMART alle NVMe.": "Selection applies to SMART and the read test; with no selection SMART checks all NVMe drives.",
    "Durchläufe (gesamter Testplan)": "Rounds (entire test plan)",
    "Aktueller Teiltest: %p%": "Current test step: %p%",
    "NVMe-Laufwerke werden gesucht …": "Scanning NVMe drives …",
    "Alle ausgewählten NVMe werden pro Durchlauf vollständig gelesen. Keine Schreibzugriffe; hohe Last und mehrstündige Laufzeit möglich. Vor jedem privilegierten Schritt kann eine Passwortabfrage erscheinen. Fortfahren?": "All selected NVMe drives are fully read in each round. No writes; high load and a runtime of several hours are possible. Each privileged step may request authentication. Continue?",
    "Der Gesamttestplan führt ausgewählte, nicht-destruktive Prüfungen nacheinander aus und erstellt ein Text- sowie ein HTML-Protokoll.": "The test plan runs the selected non-destructive checks sequentially and creates both a text and an HTML report.",
    "CPU-Stresstest (stress-ng)": "CPU stress test (stress-ng)",
    "RAM-Verifikation (stress-ng)": "RAM verification (stress-ng)",
    "RAM-Anteil des verfügbaren Speichers": "Share of available memory",
    "CPU und Arbeitsspeicher": "CPU and memory",
    "NVMe-Inventar und SMART-Gesundheit": "NVMe inventory and SMART health",
    "NVIDIA-Treiber und GPU-Inventar": "NVIDIA driver and GPU inventory",
    "NVIDIA-DCGM-Diagnose auf allen GPUs": "NVIDIA DCGM diagnostics on all GPUs",
    "DCGM ist nicht installiert; der Schritt wird nicht angeboten.": "DCGM is not installed; this step is unavailable.",
    "Datenträger und GPU": "Drives and GPU",
    "Ping-Test": "Ping test",
    "iperf3-Durchsatztest": "iperf3 throughput test",
    "IP-Adresse oder Hostname des Testziels": "IP address or hostname of the test target",
    "Bei erstem Fehler abbrechen": "Stop on first failure",
    "Protokollordner": "Report folder",
    "Ordner wählen …": "Choose folder …",
    "Ablauf und Protokoll": "Execution and report",
    "Gesamttest starten": "Start test plan",
    "Gesamttest abbrechen": "Cancel test plan",
    "HTML-Protokoll öffnen": "Open HTML report",
    "HTML-Protokoll nicht gefunden": "HTML report not found",
    "HTML-Protokoll konnte nicht geöffnet werden": "HTML report could not be opened",
    "Bitte die Datei manuell in einem Browser öffnen:\n{path}": "Please open the file manually in a browser:\n{path}",
    "Status": "Status",
    "Teiltest": "Test step",
    "Zusammenfassung": "Summary",
    "Protokollordner wählen": "Choose report folder",
    "Kein Teiltest ausgewählt": "No test step selected",
    "Bitte mindestens einen Teiltest auswählen.": "Please select at least one test step.",
    "Auf dem Zielsystem muss während des Gesamttests „iperf3 -s“ laufen. Der Standardport TCP 5201 muss erreichbar sein. Fortfahren?": "The target system must run “iperf3 -s” during the test plan. The default TCP port 5201 must be reachable. Continue?",
    "⌛ Gesamttest läuft": "⌛ Test plan running",
    "Testplan wird abgebrochen …": "Cancelling test plan …",
    "Teiltest {current} von {total}: {name}": "Test step {current} of {total}: {name}",
    "Textprotokoll: {path}": "Text report: {path}",
    "HTML-Protokoll: {path}": "HTML report: {path}",
    "✓ Gesamttest BESTANDEN": "✓ Test plan PASSED",
    "? Gesamttest ABGEBROCHEN – Protokoll wurde gespeichert": "? Test plan CANCELLED – report was saved",
    "✗ Gesamttest FEHLGESCHLAGEN": "✗ Test plan FAILED",
    "✗ Gesamttest konnte nicht abgeschlossen werden": "✗ Test plan could not be completed",
    "BESTANDEN": "PASSED",
    "FEHLGESCHLAGEN": "FAILED",
    "ABGEBROCHEN": "CANCELLED",
    "ÜBERSPRUNGEN": "SKIPPED",
    "Netzwerktest läuft …": "Network test running …",
    "✓ BESTANDEN – Durchsatz: {throughput:.1f} Mbit/s | Retransmits: {retransmits:.0f} | Messung vollständig; iperf3 meldete beim Beenden eine interne Warnung": "✓ PASSED – Throughput: {throughput:.1f} Mbit/s | Retransmits: {retransmits:.0f} | Measurement complete; iperf3 reported an internal warning while exiting",
    "Netzwerkadapter": "Network adapters",
    "Netzwerkadapter aktualisieren": "Refresh network adapters",
    "Automatisch (Routing)": "Automatic (routing)",
    "Keine Netzwerkadapter gefunden.": "No network adapters found.",
    "{speed} Mbit/s": "{speed} Mbit/s",
    "Geschwindigkeit unbekannt": "Speed unknown",
    "Adapter: {name}": "Adapter: {name}",
    "Status: {state} | Verbindung: {carrier}": "State: {state} | Link: {carrier}",
    "Treiber: {driver} | Bus: {bus}": "Driver: {driver} | Bus: {bus}",
    "Geschwindigkeit: {speed} Mbit/s | Duplex: {duplex} | MTU: {mtu}": "Speed: {speed} Mbit/s | Duplex: {duplex} | MTU: {mtu}",
    "MAC: {value}": "MAC: {value}",
    "IPv4: {value}": "IPv4: {value}",
    "IPv6: {value}": "IPv6: {value}",
    "verbunden": "up",
    "getrennt": "down",
    "wartend": "dormant",
    "untergeordnete Verbindung inaktiv": "lower layer down",
    "unbekannt": "unknown",
    "Vollduplex": "full duplex",
    "Halbduplex": "half duplex",
    "Zielhost oder IP-Adresse": "Target host or IP address",
    "Ping-Pakete": "Ping packets",
    "iperf3-Dauer": "iperf3 duration",
    "Pass/Fail-Grenzwerte": "Pass/fail thresholds",
    "Maximaler Paketverlust": "Maximum packet loss",
    "Maximale mittlere Latenz": "Maximum average latency",
    "Maximaler Jitter": "Maximum jitter",
    "Minimaler Durchsatz (0 = aus)": "Minimum throughput (0 = off)",
    "Ping-Test starten": "Start ping test",
    "iperf3-Durchsatztest starten": "Start iperf3 throughput test",
    "z. B. 192.168.1.10 oder iperf.example.net": "e.g. 192.168.1.10 or iperf.example.net",
    "Ping misst Erreichbarkeit, Latenz, Jitter und Paketverlust. Für den Durchsatztest muss auf dem Ziel ein iperf3-Server laufen.": "Ping measures reachability, latency, jitter and packet loss. An iperf3 server must run on the target for the throughput test.",
    "Zielhost fehlt": "Target host missing",
    "Bitte einen Zielhost oder eine IP-Adresse angeben.": "Please enter a target host or IP address.",
    "Netzwerktest konnte nicht starten": "Could not start network test",
    "{binary} ist nicht installiert. Bitte das Paket {package} installieren.": "{binary} is not installed. Please install the {package} package.",
    "Keine IPv4-Adresse": "No IPv4 address",
    "Der ausgewählte Adapter besitzt keine IPv4-Adresse für den iperf3-Test.": "The selected adapter has no IPv4 address for the iperf3 test.",
    "iperf3-Server auf dem Ziel erforderlich": "iperf3 server required on target",
    "Auf dem Gegensystem muss vor dem Test ein iperf3-Server laufen:\n\n  iperf3 -s\n\nmacOS (falls iperf3 fehlt): brew install iperf3\nUbuntu (falls iperf3 fehlt): sudo apt install iperf3\n\nDer Standardport ist TCP 5201 und muss von der Firewall zugelassen werden. Durchsatztest jetzt starten?": "An iperf3 server must be running on the other system before the test:\n\n  iperf3 -s\n\nmacOS (if iperf3 is missing): brew install iperf3\nUbuntu (if iperf3 is missing): sudo apt install iperf3\n\nThe default port is TCP 5201 and must be allowed through the firewall. Start the throughput test now?",
    "? ABGEBROCHEN – Netzwerktest manuell beendet": "? CANCELLED – network test stopped manually",
    "✗ FEHLER – {details}": "✗ ERROR – {details}",
    "✗ GRENZWERT ÜBERSCHRITTEN – {details}": "✗ THRESHOLD EXCEEDED – {details}",
    "✓ BESTANDEN – Durchsatz: {throughput:.1f} Mbit/s | Retransmits: {retransmits:.0f}": "✓ PASSED – throughput: {throughput:.1f} Mbit/s | retransmits: {retransmits:.0f}",
    "✓ BESTANDEN – Paketverlust: {loss:.1f}% | Latenz Ø: {average:.2f} ms (Min {minimum:.2f} / Max {maximum:.2f}) | Jitter: {jitter:.2f} ms": "✓ PASSED – packet loss: {loss:.1f}% | avg. latency: {average:.2f} ms (min {minimum:.2f} / max {maximum:.2f}) | jitter: {jitter:.2f} ms",
    "✓ BESTANDEN – Netzwerktest ohne Fehler abgeschlossen": "✓ PASSED – network test completed without errors",
    "Paketverlust {actual:.1f}% > {limit:.1f}%": "Packet loss {actual:.1f}% > {limit:.1f}%",
    "Mittlere Latenz {actual:.2f} ms > {limit:.2f} ms": "Average latency {actual:.2f} ms > {limit:.2f} ms",
    "Jitter {actual:.2f} ms > {limit:.2f} ms": "Jitter {actual:.2f} ms > {limit:.2f} ms",
    "Durchsatz {actual:.1f} Mbit/s < {limit:.1f} Mbit/s": "Throughput {actual:.1f} Mbit/s < {limit:.1f} Mbit/s",
    "Keine auswertbare Ergebniszusammenfassung": "No usable result summary",
    "Unbekannter Fehler": "Unknown error",
    "✗ iperf3-Server nicht erreichbar – Verbindung abgelehnt. Auf dem Ziel muss „iperf3 -s“ laufen; Port 5201 und Firewall prüfen.": "✗ iperf3 server unavailable – connection refused. Run ‘iperf3 -s’ on the target; check port 5201 and the firewall.",
    "✗ Zielname konnte nicht aufgelöst werden. Hostname oder DNS prüfen.": "✗ Target name could not be resolved. Check the hostname or DNS.",
    "✗ Keine Route zum Ziel. IP-Adresse, Adapterauswahl und Routing prüfen.": "✗ No route to the target. Check the IP address, selected adapter and routing.",
    "✗ Netzwerk nicht erreichbar. Linkstatus und IP-Konfiguration prüfen.": "✗ Network unreachable. Check link state and IP configuration.",
    "✗ Zeitüberschreitung. Zielsystem, Firewall und Netzwerkverbindung prüfen.": "✗ Timed out. Check the target system, firewall and network connection.",
    "✗ Netzwerkzugriff nicht erlaubt. Berechtigungen des Werkzeugs prüfen.": "✗ Network access is not permitted. Check the tool permissions.",
    "✗ Verbindung zum iperf3-Server fehlgeschlagen. Auf dem Ziel „iperf3 -s“ sowie Port 5201 und Firewall prüfen.": "✗ Could not connect to the iperf3 server. Run ‘iperf3 -s’ on the target and check port 5201 and the firewall.",
    "Allokiert den gesamten verfuegbaren RAM, schreibt in jedem Zyklus wechselnde Bitmuster (0xAA, 0x55, 0xFF, 0x00, Walking-Bits sowie zyklus-abhaengige Wort- und Zufallsmuster), verifiziert, gibt frei und wiederholt. Findet defekte Speicherzellen und Timing-Fehler im RAM.": "Allocates all available RAM, writes alternating bit patterns in every cycle (0xAA, 0x55, 0xFF, 0x00, walking bits plus cycle-dependent word and random patterns), verifies them, releases the memory and repeats. Detects faulty RAM cells and timing errors.",
    "CPU-Temperatur": "CPU temperature",
    "CPU + RAM kombiniert": "Combined CPU + RAM",
    "CPU-Cache-Stress": "CPU cache stress",
    "CPU-Last (maximale Hitze)": "CPU load (maximum heat)",
    "Operationen": "Operations",
    "Pause je Zyklus": "Pause per cycle",
    "Schreib-/Lesedurchläufe": "Write/read passes",
    "Speicher": "Memory",
    "Abgebrochen": "Cancelled",
    "Alle abwählen": "Clear selection",
    "Datenträger aushängen…": "Unmount drive…",
    "Nach Updates suchen": "Check for updates",
    "Update-Prüfung fehlgeschlagen: {error}": "Update check failed: {error}",
    "Hardwaretest {version} ist aktuell.": "Hardwaretest {version} is up to date.",
    "Update verfügbar": "Update available",
    "Hardwaretest {new} ist verfügbar (installiert: {current}).": "Hardwaretest {new} is available (installed: {current}).",
    "Herunterladen": "Download",
    "Später": "Later",
    "Diese Version nicht mehr anzeigen": "Don't show this version again",
    "Alle Cores anzeigen": "Show all cores",
    "Alle Cores ausblenden": "Hide all cores",
    "Core-Details anzeigen": "Show core details",
    "Core-Details anzeigen ({count}, max. {page_size}/Seite)": "Show core details ({count}, max. {page_size}/page)",
    "Core-Details ausblenden ({count}, max. {page_size}/Seite)": "Hide core details ({count}, max. {page_size}/page)",
    "Vorherige": "Previous",
    "Nächste": "Next",
    "Seite {page}/{pages} – Cores {first}–{last} von {count}": "Page {page}/{pages} – cores {first}–{last} of {count}",
    "Aufgabe": "Task",
    "Ausgewogener RAM/CPU-Test": "Balanced RAM/CPU test",
    "Binary": "Binary",
    "Blockgröße": "Block size",
    "Blockgroesse": "Block size",
    "Bereit": "Ready",
    "Bereit (destruktiv)": "Ready (destructive)",
    "Bereit (heuristisch)": "Ready (estimated)",
    "btop starten": "Start btop",
    "CPU-Kerne": "CPU cores",
    "Datenträger scannen": "Scan drives",
    "fio erstellt/löscht ausschließlich die angegebene Datei. Keine bestehenden Daten werden verändert.": "fio only creates and deletes the specified file. Existing data is not changed.",
    "Dateigröße": "File size",
    "Dateibasierter Test (nicht-destruktiv)": "File-based test (non-destructive)",
    "DESTRUKTIVEN Test starten": "Start DESTRUCTIVE test",
    "Destruktiver Schreib-/Lesetest (pkexec + verify)": "Destructive write/read test (pkexec + verify)",
    "Destruktiver Test fertig": "Destructive test finished",
    "Disk (Datei)": "Disk (file)",
    "Disk (Destruktiv)": "Disk (destructive)",
    "Disk (Geräte)": "Disk (devices)",
    "Dauer": "Duration",
    " Durchläufe": " passes",
    "Direktmodus": "Direct mode",
    "ECC / EDAC": "ECC / EDAC",
    "Fertig": "Finished",
    "Fehler": "Errors",
    "FEHLER GEFUNDEN": "ERRORS FOUND",
    "fio starten": "Start fio",
    "Fortschritt: 0%": "Progress: 0%",
    "Fortschritt (Write+Verify): 0%": "Progress (write + verify): 0%",
    "Heuristischer Fortschritt: 0%": "Estimated progress: 0%",
    "Hilfe / Help": "Help",
    "HPE RAID-Info": "HPE RAID information",
    "GUI-Kerne freilassen": "Leave GUI cores free",
    "Informationen": "Information",
    "Hinweis: Der Test liest alle Blöcke mit pkexec/fio, schreibt aber nichts. Passwortabfrage möglich.": "Note: The test reads every block with pkexec/fio but does not write anything. A password prompt may appear.",
    "Jobs": "Jobs",
    "Kernel-Logs": "Kernel logs",
    "Keine Sensoren gefunden": "No sensors found",
    "MCE-Logs": "MCE logs",
    "Manuell gestoppt": "Stopped manually",
    "Maximale CPU-Hitze, wenig RAM": "Maximum CPU heat, little RAM",
    "Minuten pro FFT": "Minutes per FFT",
    "Modus": "Mode",
    "memtest86+ pruefen": "Check memtest86+",
    "Nur mprime -t (bestehende local.txt nutzen)": "Only mprime -t (use existing local.txt)",
    "Noch keine Datenträger gescannt.": "No drives scanned yet.",
    "Pfad wählen": "Choose path",
    "Prime95 starten": "Start Prime95",
    "RAM-Fuelltest": "RAM fill test",
    "RAM-Fuelltest starten": "Start RAM fill test",
    "RAM-Bandbreite (Durchsatz)": "RAM bandwidth (throughput)",
    "RAM-Test (Fehler finden)": "RAM test (find errors)",
    "RAM gesamt: {memory} MB": "Total RAM: {memory} MB",
    "RAM verfuegbar: {memory} MB": "Available RAM: {memory} MB",
    "Reserve (OS/GUI)": "Reserve (OS/GUI)",
    "Report als Webseite": "Report as web page",
    "Report öffnen": "Open report",
    "Rohgeräte-Lesetest (heuristisch)": "Raw-device read test (estimated)",
    "Rohgeräte-Lesetest fertig (heuristisch)": "Raw-device read test finished (estimated)",
    "Rohgeräte-Read starten": "Start raw-device read",
    "SMART-Info": "SMART information",
    "Speichercontroller": "Memory controller",
    "Speichercontroller-Test starten": "Start memory-controller test",
    "Stressoren:": "Stressors:",
    "stress-ng starten": "Start stress-ng",
    "Systemstatus": "System status",
    "Stop": "Stop",
    "Swap aktivieren": "Enable swap",
    "Swap bereits aktiv": "Swap already enabled",
    "Swap bereits deaktiviert": "Swap already disabled",
    "Swap deaktivieren": "Disable swap",
    "Swap wird aktiviert …": "Enabling swap …",
    "Swap wird deaktiviert …": "Disabling swap …",
    "Swap aktiv: {state}": "Swap enabled: {state}",
    "Systemdaten aktualisieren": "Refresh system data",
    "Systemdaten werden ermittelt...": "Collecting system data...",
    "Systemreport (JSON)": "System report (JSON)",
    "Systemwerte aktualisieren": "Refresh system values",
    "Test fertig": "Test finished",
    "Testdatei": "Test file",
    "Testmodus": "Test mode",
    "Kerne logisch: {cores}": "Logical cores: {cores}",
    "Kerne physisch: {cores}": "Physical cores: {cores}",
    "Threads": "Threads",
    "Worker Threads": "Worker threads",
    "Workload": "Workload",
    "ja": "yes",
    "nein": "no",
    "Wird ermittelt…": "Collecting data…",
    "WARNUNG: Dieser Test überschreibt alle ausgewählten Datenträger vollständig und vergleicht die Daten erneut. Nur auf leeren Laufwerken verwenden!": "WARNING: This test completely overwrites all selected drives and then verifies the data. Use only on empty drives!",
    "Über Hardwaretest": "About Hardwaretest",
    "Über Hardwaretest / About": "About Hardwaretest",
    "Sprache": "Language",
}

_EN_PARTS: tuple[tuple[str, str], ...] = (
    ("Fortschritt", "Progress"),
    ("Heuristischer", "Estimated"),
    ("Verfuegbar", "Available"),
    ("Kerne", "Cores"),
    ("logisch", "logical"),
    ("physisch", "physical"),
    ("Reserviert fuer GUI", "Reserved for GUI"),
    ("Kern(e)", "core(s)"),
    ("deaktiviert", "disabled"),
    ("aktiv", "enabled"),
    ("Datenträger gefunden", "drives found"),
    ("Keine geeigneten Datenträger gefunden", "No suitable drives found"),
    ("BESTANDEN", "PASSED"),
    ("FEHLER GEFUNDEN", "ERRORS FOUND"),
    ("Keine Fehler gefunden", "No errors found"),
    ("Manuell gestoppt", "Stopped manually"),
    ("Alle Cores anzeigen", "Show all cores"),
    ("Alle Cores ausblenden", "Hide all cores"),
    ("Gesamt", "Overall"),
    ("Korrigierbare Fehler", "Correctable errors"),
    ("Unkorrigierbare Fehler", "Uncorrectable errors"),
    ("krit", "critical"),
)


class LanguageManager(QObject):
    """Keep one persisted language setting and notify the complete UI."""

    language_changed = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        saved = QSettings("Hardwaretest", "Hardwaretest").value("language", "de")
        self._language = saved if saved in {"de", "en"} else "de"

    @property
    def language(self) -> str:
        return self._language

    def set_language(self, language: str) -> None:
        if language not in {"de", "en"} or language == self._language:
            return
        self._language = language
        QSettings("Hardwaretest", "Hardwaretest").setValue("language", language)
        self.language_changed.emit(language)

    def tr(self, german: str, **values: Any) -> str:
        text = german
        if self._language == "en":
            text = _EN.get(german, german)
            if text == german:
                for source, translated in _EN_PARTS:
                    text = text.replace(source, translated)
        return text.format(**values) if values else text

    def retranslate_widget_tree(self, root: QWidget) -> None:
        """Translate normal Qt labels, buttons, groups, tabs and menus.

        Widgets retain their original German text in a Python attribute so
        switching back to German is lossless.  Panels only need explicit
        updates for text that is calculated while a test is running.
        """
        for widget in (root, *root.findChildren(QWidget)):
            if getattr(widget, "_hardwaretest_skip_tree", False):
                continue
            if isinstance(widget, (QLabel, QAbstractButton)):
                # Runtime result labels retain their German source text and
                # parameters separately.  Their panel retranslates them on
                # language changes; treating the currently displayed English
                # text as a new German source would make switching back lossy.
                if getattr(widget, "_hardwaretest_runtime_text", None) is None:
                    self._translate_property(widget, "text", widget.text, widget.setText)
                self._translate_property(widget, "tool_tip", widget.toolTip, widget.setToolTip)
            if isinstance(widget, QGroupBox):
                self._translate_property(widget, "title", widget.title, widget.setTitle)
            if isinstance(widget, QComboBox):
                self._translate_combo(widget)
            if isinstance(widget, QLineEdit):
                self._translate_property(
                    widget, "placeholder_text", widget.placeholderText, widget.setPlaceholderText
                )
            if isinstance(widget, QSpinBox):
                self._translate_property(widget, "suffix", widget.suffix, widget.setSuffix)
            if isinstance(widget, QMenu):
                self._translate_property(widget, "title", widget.title, widget.setTitle)
                for action in widget.actions():
                    self._translate_action(action)
            if isinstance(widget, QTabWidget):
                self._translate_tabs(widget)

        # Menus are children of QMainWindow but QAction is not a QWidget.
        for action in root.findChildren(QAction):
            self._translate_action(action)

    def _translate_property(self, obj: QObject, name: str, getter: Any, setter: Any) -> None:
        attribute = f"_hardwaretest_{name}_de"
        if not hasattr(obj, attribute):
            setattr(obj, attribute, getter())
        original = getattr(obj, attribute)
        if original:
            setter(self.tr(original))

    def _translate_action(self, action: QAction) -> None:
        self._translate_property(action, "text", action.text, action.setText)

    def _translate_tabs(self, tabs: QTabWidget) -> None:
        if not hasattr(tabs, "_hardwaretest_tabs_de"):
            tabs._hardwaretest_tabs_de = [tabs.tabText(i) for i in range(tabs.count())]
        for index, text in enumerate(tabs._hardwaretest_tabs_de):
            tabs.setTabText(index, self.tr(text))

    def _translate_combo(self, combo: QComboBox) -> None:
        if not hasattr(combo, "_hardwaretest_items_de"):
            combo._hardwaretest_items_de = [combo.itemText(i) for i in range(combo.count())]
        for index, text in enumerate(combo._hardwaretest_items_de):
            combo.setItemText(index, self.tr(text))


language_manager = LanguageManager()
