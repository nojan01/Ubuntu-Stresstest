# Architekturüberblick

## Portable Auslieferung

Für DEB und AppImage bündelt PyInstaller den Python-Interpreter, PySide6 und die
Python-Module in einem eigenständigen Programmverzeichnis. Zur Laufzeit werden
Ressourcen über `hardwaretest.core.paths.resource_path()` aufgelöst; die Funktion
arbeitet sowohl im Quellbaum als auch im entpackten PyInstaller-Verzeichnis.
Das Zielsystem benötigt deshalb weder eine Python-venv noch `pip` oder eine
bestimmte System-Python-Version.

Systemnahe Testprogramme und Bibliotheken bleiben externe Paketabhängigkeiten.
Beim DEB installiert APT diese aus den Paketquellen der jeweiligen Ubuntu-
Version. Beim distributionsübergreifenden AppImage stellt der Administrator sie
über die Paketverwaltung der Ziel-Distribution bereit. Dadurch bleiben Werkzeuge
wie `fio`, `stress-ng`, `nvme-cli` und die NVIDIA-Treiber passend zum Kernel und
zur Distribution aktualisierbar.

```
hardwaretest/
├── core/          # Systeminfos, generische Runner-Abstraktionen
├── tests/         # Konkrete Tool-Adapter wie stress-ng, mprime
├── ui/            # PySide6 Oberflächenschicht
└── ui/widgets/    # Wiederverwendbare Widgets (stress-ng-, Prime95-, Speichercontroller-, Disk- und Info-Panels)
```

- UI-Widgets erzeugen für jeden Start passende `TestParameters` und übergeben sie an spezialisierte Runner (`StressNgRunner`, `MprimeRunner`, `MemoryControllerRunner`, `FioRunner`, `FioDeviceSweepRunner`).
- Runner erben von `BaseTestRunner`, kapseln die jeweilige CLI (`subprocess`) und streamen Logs zurück ins UI.
- Systeminformationen werden vor jedem Start geprüft (RAM-Freiheit, CPU-Kerne, Swap), damit Kerne für die GUI reserviert bleiben und Tests nicht das System blockieren.
- Zwei Disk-Tabs trennen Dateitests und Rohgeräte-Reads: der Dateitab erzeugt temporäre Dateien pro Lauf, der Gerätetab scannt Blockgeräte via `lsblk -J`, erlaubt die Auswahl einzelner Disks und führt parallele Read-Jobs aus; Rohgeräte-Läufe starten automatisch über `pkexec fio`. Ein dritter Tab kapselt destruktive Write+Verify-Workloads (ebenfalls via pkexec) und erzwingt Sicherheitsabfragen.
- Der Netzwerk-Tab erfasst Adapterdaten lesend aus sysfs und `ip -j`. Clientseitige, nicht-destruktive Tests laufen ausschließlich zum vom Bediener angegebenen Ziel: Ping liefert Paketverlust, Latenz und Jitter, `iperf3` Durchsatz und Retransmits. Ein gewählter Adapter wird explizit gebunden; konfigurierbare Grenzwerte ergeben Pass/Fail. Es werden weder eigene Server gestartet noch feste Internetziele kontaktiert.
- Der NVMe-Tab erkennt NVMe-Controller mit `nvme-cli`, zeigt Modell, Seriennummer und Firmware, liest SMART-/Gesundheitsdaten aus und wertet das Selbsttest-Protokoll als bestanden/fehlgeschlagen aus. Die dedizierten NVMe-Lesetests verwenden fest `rw=read`: ein zeitlich begrenzter Benchmark liefert Durchsatz, IOPS und Latenz; der vollständige Lese-Test liest alle Blöcke ohne Schreib- oder Verifikationsoptionen. Privilegierte Befehle nutzen bei Bedarf den grafischen Polkit-Dialog.
- Der NVIDIA-GPU-Tab erfasst alle vom Treiber sichtbaren GPUs über `nvidia-smi`, einschließlich Takt- und PCIe-Linkdaten. Ein eigener Hintergrund-Worker aktualisiert Auslastung, Temperatur, VRAM, Leistungsaufnahme und Taktraten, ohne den Qt-Hauptthread zu blockieren. Optional startet der Tab NVIDIA-DCGM-Diagnosestufen für eine per Checkbox gewählte GPU-Menge, aktiviert dabei automatisch die Liveüberwachung und wertet Exitcode sowie die versionsabhängige JSON-Ausgabe aus.
- Der Gesamttestplan läuft vollständig in einem Qt-Workerthread und orchestriert die vorhandenen, nicht-destruktiven Runner sequenziell. Ein Cancel-Event beendet lange Teiltests kontrolliert. Jeder Schritt liefert einen strukturierten Status, Zeitstempel, eine Zusammenfassung und Detailzeilen; daraus entstehen auch bei Abbruch eigenständige UTF-8-Text- und HTML-Berichte. Der HTML-Bericht enthält keine externen Ressourcen und besitzt ein Druck-Stylesheet für den PDF-Export im Browser.
