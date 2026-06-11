# Architekturüberblick

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
