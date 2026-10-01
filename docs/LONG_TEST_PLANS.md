# Langzeittestpläne (0.2.30)

## Netzwerk-Grenzwerte

Ping bewertet maximalen Paketverlust, mittlere Latenz und Jitter (RTT-mdev von
Linux ping); iperf3 bewertet den Mindestdurchsatz. Die Werte werden im Gesamttest
vor dem Start eingestellt und gelten für **jeden** Durchlauf. Startwerte: 0 %
Paketverlust, 100 ms mittlere Latenz, 20 ms Jitter, Mindestdurchsatz 0 = ausgeschaltet.
Bei den maximalen Werten bedeutet 0 einen strikten Null-Grenzwert, nicht „aus“.
Fehlende/ungültige Messwerte werden nicht als erfolgreich bewertet. Ein
Grenzwertfehler bleibt auch bei Exit-Code 0 ein fehlgeschlagener Teiltest und wird
in Zusammenfassung/Details genannt. „Bei erstem Fehler abbrechen“ greift auch hier.

Individuelle Temperaturgrenzen werden über die gleichnamige Schaltfläche geändert
und gelten im aktivierten Begleitmonitoring ebenso wie im eigenen Monitoring-Tab.
Die Monitoring-Auswertung samt begrenzten Verlaufsdiagrammen steht im zusätzlichen
Monitoring-HTML-Bericht; dessen Pfad erscheint im Gesamttestbericht.

Im Tab **Gesamttest** werden die ausgewählten Teiltests nacheinander ausgeführt.
**Durchläufe (gesamter Testplan)** wiederholt diesen Ablauf 1–10.000 Mal. Die
Laufzeit ergibt sich aus den gewählten Teiltests und Wiederholungen; es gibt noch
keine feste Endzeit. Beispielsweise dauern 24 Durchläufe mit je 30 Minuten CPU und
30 Minuten RAM mindestens einen Tag, zuzüglich weiterer Tests und Anmeldungen.
„Bei erstem Fehler abbrechen“ beendet auch alle weiteren Wiederholungen. Ein
vollständig ausgeführter, aber fehlerhafter Durchlauf zählt als vollständig, nicht
als bestanden. Ein früher Fehler bleibt im Endergebnis erhalten, auch wenn der
letzte Durchlauf bestanden wurde. Die App muss offen bleiben; kein Fortsetzen nach
Neustart und keine Garantie für völlig unbeaufsichtigte Ausführung.

## NVMe im Gesamttest

1. **NVMe-Auswahl aktualisieren** lädt Namespaces im Hintergrund. Die scrollbare
   Liste bleibt auch bei vielen Laufwerken begrenzt groß. Laufwerke per Checkbox
   oder **Alle auswählen** wählen. Die Auswahl gilt für SMART und den Lesetest.
   Ohne Auswahl prüft der normale SMART-Schritt weiterhin alle gefundenen NVMe.
2. **Vollständiger NVMe-Lesetest (optional)** aktivieren und den Start bestätigen.
   Standardmäßig ist der neue Lasttest ausgeschaltet. Benötigt `nvme-cli`, `fio`
   und Administratorrechte (über `pkexec`, sofern nicht bereits root).
3. Vor jedem Lauf werden Pfad, Seriennummer, Modell und Kapazität mit der aktuellen
   Erkennung verglichen. Fehlende/gewechselte Laufwerke führen zum Fehler; es wird
   nicht still auf eine andere Platte gewechselt. SMART wird vor und nach dem
   Lesetest je Controller gelesen.
4. `fio` liest alle Blöcke der ausgewählten Namespaces parallel mit `rw=read`,
   `size=100%`, ohne Zeitlimit, mit `--readonly` und `allow_file_create=0`. Der
   zusätzliche Schreibschutz verhindert Schreib-/Trim-Workloads. Es werden keine
   Testdateien erzeugt und keine Daten oder Dateisysteme repariert. Der Test darf
   eingehängte NVMe lesen, belastet aber das System und kann viele Stunden dauern.
5. Ein getrennter Fortschrittsbalken zeigt den aktuellen Teiltest, zusätzlich zum
   Gesamtfortschritt. Fehler/Abbruch gelten nicht als vollständig bestandener
   Lesetest. Stoppen oder thermischer Sicherheitsabbruch beendet den fio-Prozess;
   die Administratorabfrage zum Stoppen kann erneut erforderlich sein.
6. Das Protokoll nennt Gerät, Modell, Seriennummer, Firmware und SMART vorher/nachher:
   Temperatur, Medienfehler, Fehlerlog-Einträge, kritische Warnungen, Verschleiß und
   gelesene Dateneinheiten. Kritische Warnungen oder Medienfehler verhindern ein
   bestandenes Ergebnis. Ein fehlender Medienfehlerzähler wird nicht als Null
   interpretiert. Fehlerlog-Einträge allein beweisen keinen Medienschaden (z.B.
   können nicht unterstützte Verwaltungsbefehle solche Einträge erzeugen).

Ein Lesetest prüft Lesbarkeit/I/O-Fehler, **nicht** die inhaltliche Richtigkeit von
Dateien, Dateisystemstruktur oder alle intern versteckten NAND-Zellen. NVMe hinter
USB-Bridges, die keine NVMe-Namespaces bereitstellen, benötigen den allgemeinen
Laufwerkstest; SATA/SAS-/USB-Lesetests im Gesamttest sind noch offen.

## Berichte und Sicherheit

- CSV: eine fortlaufend geschriebene und nach jedem Teiltest gespülte Zeile mit
  Durchlauf, Teiltest, Status, Zeitstempeln, Dauer und Zusammenfassung. Keine
  Einzelpakete oder Blockmeldungen. Bei App-/Systemabsturz bleibt die CSV bis zum
  letzten abgeschlossenen Teiltest; TXT/HTML werden erst bei Ende/normalem Abbruch
  erzeugt. Ein Stromausfall kann trotzdem noch nicht dauerhaft gespeicherte Daten
  verlieren.
- TXT/HTML: je Teiltest Statuszähler, Dauer aller Ausführungen, letztes Ergebnis und
  erster Fehler. Begrenzte Detailausgaben verhindern tageweise wachsende Berichte.
  Die CSV hält die einzelnen Ausführungen nachvollziehbar fest. Der HTML-Bericht
  ist eigenständig und im Browser als PDF druckbar.
- Begleitmonitoring läuft über alle Durchläufe, erzeugt seine eigene Mess-CSV und
  kompakte Berichte; Temperatur-/ECC-Sicherheitsabbruch gilt weiter. Fehlende
  Sensoren bedeuten fehlende Überwachung, keine Zusicherung gesunder Hardware.
- Kühlung, Speicherplatz und Backups sicherstellen. Lasttests nie parallel zu
  wichtigen Produktionsarbeiten oder Datenrettung betreiben. Keine automatischen
  Dateisystemreparaturen und keine Pool-Imports/-Änderungen im Testplan.
- Allgemeine Tests gelten für PCs, Workstations und Server; es gibt keine neuen
  HPE-spezifischen Anforderungen. Ubuntu 24.04/26.04 sind die Zielplattformen;
  die eingebettete Python-Laufzeit bleibt erhalten. Tests mit simulierten Daten
  ersetzen keinen mehrtägigen Lastlauf und keinen Praxistest auf Ubuntu 26.04.

[fio: --readonly](https://fio.readthedocs.io/en/latest/fio_doc.html#cmdoption-readonly)

English: The **Test plan** supports 1–10,000 rounds and an optional, explicitly
selected parallel full NVMe read test. Drive identity is rechecked; SMART is read
before/after. `fio --readonly`, fixed read-only jobs and no file creation guard
against writes. Separate step/plan progress, compact TXT/HTML status counts and
one CSV row per step/round. Failed early rounds cannot be hidden by later passes.
Cancellation saves partial results. Background monitoring spans all rounds.
Privileged steps may request authentication again, including stopping fio. No
filesystem repairs, ZFS imports, automatic resume or fixed end time. Keep the app
open, provide cooling/report storage, and avoid production/data-recovery workloads.
