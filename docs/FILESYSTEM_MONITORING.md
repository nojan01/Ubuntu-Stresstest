# Dateisystemprüfung und Langzeitmonitoring (Stand 0.2.61)

> **Seit 0.2.61 repariert das Programm keine Dateisysteme mehr.** Alle Prüfungen sind
> strikt lesend. Reparaturen bleiben eine bewusste Administrator-Entscheidung auf der
> Kommandozeile; das HPE-Live-ISO bringt die Werkzeuge dafür mit (e2fsprogs, xfsprogs,
> btrfs-progs, dosfstools, ntfs-3g, exfatprogs, zfsutils-linux, gdisk, testdisk).
> Die nachfolgenden Abschnitte zu „Reparieren …“ beschreiben den Stand bis 0.2.60.

## Individuelle Temperaturgrenzen und Diagramme (0.2.30)

**Individuelle Temperaturgrenzen …** im Monitoring oder Gesamttest liest die
verfügbaren Sensoren im Hintergrund. Nur mit der Checkbox aktivierte Zeilen
überschreiben den allgemeinen Standardwert. Der Grenzwert gilt inklusiv: ein Wert
gleich der Grenze löst ebenso aus wie ein höherer. Einstellbar sind 30–120 °C;
das sind Eingabegrenzen, **keine** empfohlenen sicheren Temperaturen. Hersteller-
angaben für CPU, GPU, SSD usw. beachten. Ein individuell höherer Grenzwert darf
den Standardwert bewusst überschreiben; dies vor Lasttests sorgfältig prüfen.

Die Profile werden pro Benutzer in den Hardwaretest-Einstellungen gespeichert
und von Langzeit- und Begleitmonitoring gemeinsam verwendet. Grenzen sind für die
Dauer eines Laufs eingefroren; erst vor einem neuen Lauf ändern. Abbrechen im
Dialog verändert das gespeicherte Profil nicht. Nicht verfügbare Profileinträge
bleiben erhalten, werden im Bericht separat genannt und **nicht** überwacht.
Bei ungültigen/unlesbaren Einstellungen wird der Start blockiert; über den Dialog
das Profil prüfen und gegebenenfalls neu speichern. Bei geänderter Hardware oder
geänderten Sensorbezeichnungen/-Reihenfolgen die Zuordnung erneut kontrollieren:
Profile passen auf den exakten Messschlüssel, nicht auf eine garantierte Geräte-ID.

Im Monitoring lässt sich eine Messreihe als Live-Diagramm wählen. Die Diagramme
arbeiten mit verstrichener Zeit (Sekunden/Minuten/Stunden), unabhängig von
Änderungen der Systemuhr. Ältere Zeitabschnitte werden zusammengefasst: Bänder
zeigen Min/Max, Punkte/Linien den nach Messanzahl gewichteten Mittelwert. Dadurch
bleiben **gemessene** kurze Spitzen bei Verdichtung erhalten. Spitzen zwischen
zwei Messungen können nicht erkannt werden. Fehlende/ungültige Messwerte werden
nicht als Null eingetragen; Linien werden an Messlücken unterbrochen. Nach starker
Verdichtung kann eine einzelne Lücke einen ganzen Zeitabschnitt als lückenhaft
kennzeichnen; die CSV enthält die erfassten Einzelwerte und Ausfälle.

Für höchstens 256 Messreihen werden je maximal 240 Zeitabschnitte gehalten;
weitere Messreihen werden weiterhin überwacht und vollständig in CSV erfasst.
Der HTML-Bericht enthält höchstens 24 eingebettete SVG-Diagramme (Temperaturen und
weitere Messgrößen), benötigt kein Internet und keine externen Bibliotheken.
TXT enthält weiterhin Statistik, Grenzen und begrenzte Ereignisdetails; CSV den
vollständigen erfassten Verlauf. Ein Diagramm ist kein SMART-Verlauf: wiederholte
SMART-Abfragen sind noch offen. HPE-spezifische Werkzeuge sind hierfür nicht nötig.

English: Individual temperature overrides are saved per user and shared by
standalone and accompanying monitoring. Only checked sensors override the default;
follow hardware specifications and recheck sensor mappings after hardware changes.
Missing configured sensors are explicitly reported, not monitored. Live and
offline HTML SVG charts compact time buckets while preserving measured min/max
and sample-weighted means; missing data break lines, never become zero. Charts use
elapsed time. Up to 256 histories × 240 buckets in memory, up to 24 HTML charts;
all recorded metrics remain in CSV. No new Python/plotting dependency and no
HPE-specific requirements. SMART time-series collection is still pending.

## ZFS: Pool-Status und Scrub

Unter **Dateisystem → ZFS-Pools** werden mit `zpool list` bereits importierte
Pools erkannt. ZFS wird auf Pool-Ebene geprüft, nicht mit fsck auf einer einzelnen
`zfs_member`-Partition. Eine solche Signatur allein beweist keinen aktiven Pool;
die App importiert/erzeugt keine Pools und verändert keine ZFS-Eigenschaften.

**Pool-Status lesen** zeigt Zustand, letzte/aktuelle Scrub-Ergebnisse und die
Lese-, Schreib- und Prüfsummenfehler der Laufwerke aus `zpool status -P -p`.
Ein vorhandener Scrub oder ein pausierter Scrub kann überwacht werden. Ein
Resilver (Wiederherstellung nach Laufwerksersatz) wird als solcher angezeigt,
nicht als bestandener Scrub bewertet.

**Scrub starten / fortsetzen …** prüft alle im Pool gespeicherten Datenblöcke und
ihre Prüfsummen. Der Pool darf dabei eingehängt sein, auch ein ZFS-Systempool.
Der Scrub kann Stunden dauern und die Laufwerke stark belasten. Mit redundanten
Kopien (z.B. Mirror/RAIDZ) kann ZFS beschädigte Daten automatisch korrigieren;
ohne brauchbare Kopie kann es einen Schaden nur melden. Ein Scrub ist deshalb
kein garantiert rein lesender Test und ersetzt kein Backup. Vor dem Start
erfolgen Bestätigung und Administratoranmeldung. Der privilegierte Helfer prüft
die Pool-GUID erneut, blockiert bereits laufende Scrubs/Resilver und verhindert
einen Start auf einem schreibgeschützt importierten oder nicht verfügbaren Pool.

**Scrub stoppen …** führt nach Bestätigung `zpool scrub -s` aus. Ein abgebrochener
Lauf liefert kein vollständiges Prüfergebnis. **Nur Überwachung beenden** stoppt
dagegen nur die App-Aufzeichnung: Der Scrub läuft im ZFS-Kernel weiter. Ein normaler
App-Abschluss wird erst nach Ende der Überwachung zugelassen. Über den Status kann
der weiterlaufende Scrub später erneut überwacht werden. Der Temperaturwächter
stoppt einen ZFS-Scrub nicht automatisch; dafür die eigene Stop-Schaltfläche nutzen.

Alle fünf Sekunden wird der Status im Hintergrund abgefragt. Der Prozentwert ist
eine Schätzung von ZFS und wird während des Laufs höchstens als 99 % angezeigt;
100 % erscheinen erst beim bestätigten Abschluss. Bei fehlenden Angaben wird
eine laufende Aktion statt eines erfundenen Prozentwertes angezeigt. Text/HTML
enthalten Start- und Endstatus und das Ergebnis; die CSV zeichnet den Verlauf
fortlaufend auf. Alte Scrub-Ergebnisse und unvollständige Überwachung werden nicht
als erfolgreicher neuer Test gewertet. Verbleibende Gerätefehler und degradierte
Pools werden auch nach einem abgeschlossenen Scrub als Warnung/Fehler ausgewiesen.

Voraussetzung: `sudo apt install zfsutils-linux` und ein passendes, geladenes
ZFS-Kernelmodul. Auf einem bestehenden ZFS-System ist dies normalerweise vorhanden.
Fehlende Werkzeuge oder fehlende Kernelunterstützung werden angezeigt; die App
installiert/lädt sie nicht automatisch. Es werden gemeinsame Optionen von
Ubuntu 24.04 und 26.04 verwendet, keine nur in neueren ZFS-Versionen vorhandene
JSON-Ausgabe. Mangels ZFS-Pool auf diesem Entwicklungsrechner wurden Auswertung,
Steuerung und Fehlerfälle mit simulierten ZFS-Ausgaben getestet; ein echter Scrub
auf ZFS und ein vollständiger Lauf auf Ubuntu 26.04 stehen noch aus.

[OpenZFS Scrub](https://openzfs.github.io/openzfs-docs/man/master/8/zpool-scrub.8.html)
und [Ubuntu 24.04 Scrub](https://manpages.ubuntu.com/manpages/noble/man8/zpool-scrub.8.html).

English: **Filesystem → ZFS pools** offers imported pool selection, pool/vdev error
status, explicit scrub start/resume/stop, progress and TXT/HTML/CSV reports. Scrub
checks allocated data/checksums online and may automatically repair damage from
redundant copies. Confirmation and administrator authentication are required for
control. No pool import/create, no fsck on ZFS members, no automatic kernel/tool
installation. Stopping monitoring leaves the kernel scrub running. Existing
scrubs/resilvers, paused/cancelled scans, stale results and read-only pools are
handled separately. Requires zfsutils-linux and matching ZFS kernel support.

## Eingehängte Dateisysteme: Statusdiagnose

**Statusdiagnose (eingehängt)** funktioniert auch auf dem laufenden System und
der FAT-EFI-Partition. Sie liest Einhängepunkte, Optionen, Kapazität/verfügbaren
Speicher, den ext4-Fehlerzähler (bei ext4) sowie gerätebezogene Fehlerhinweise in
maximal den letzten 2000 Kernelmeldungen des aktuellen Systemstarts. Fehlende
Datenquellen werden im Bericht ausgewiesen. Es werden keine Testdateien angelegt,
kein fsck auf dem eingehängten Gerät gestartet und nichts repariert.

Das Ergebnis ist eine begrenzte Statusdiagnose, keine vollständige Prüfung der
Dateisystemstruktur. **Struktur prüfen (offline)** bleibt für eingehängte
Dateisysteme gesperrt (Ausnahme: schreibgeschütztes squashfs).

Die Schaltfläche **HTML-Protokoll öffnen** verwendet einen installierten
Webbrowser, bevorzugt den konfigurierten Standardbrowser. Die HTML-Dateizuordnung
wird umgangen, damit beispielsweise ChatGPT nicht versehentlich gestartet wird.

## Für PCs und Server

Diese Funktionen benötigen keinen HPE-Server. **HPE Smart Array / RAID-Info**
ist dagegen ausschließlich für passende HPE-Controller und deren optionales
`ssacli`/`hpssacli` gedacht. Fehlende HPE-Hardware ist kein Fehler eines Home-PCs.
Remote-iLO/Redfish-Diagnose, Netzteilredundanz und weitere herstellerspezifische
Prüfungen sind noch nicht Bestandteil dieser Ausbaustufe.

## Dateisystem: sicherer Ablauf

1. Backup erstellen. Bei vermuteten Hardwaredefekten zuerst ein Laufwerksabbild
   erstellen lassen; Reparaturen können die spätere Datenrettung erschweren.
2. Im Tab **Dateisystem** die Laufwerke aktualisieren. Pfad, Typ, UUID und
   Einhängepunkte werden angezeigt. Unterstützt sind zunächst ext2/ext3/ext4.
3. Eingehängte Dateisysteme bleiben für die **Strukturprüfung gesperrt**, auch
   bei schreibgeschützten oder Bind-Mounts. Für die Systempartition ein Live-USB
   starten. Nicht automatisch einhängen lassen. Keine parallelen Zugriffe anderer
   Programme, anderer Benutzer, Container oder eines zweiten Rechners zulassen.
4. **Struktur prüfen (offline)** führt `e2fsck -f -n` mit Administratoranmeldung aus. Dabei wird
   nichts repariert. Bei ausstehender Journal-Wiederherstellung ist ein reiner
   Lesecheck nicht verlässlich: Das Ergebnis wird nicht als fehlerfrei bewertet.
5. **Reparatur** erfolgt nicht in der App (seit 0.2.61). Gemeldete Fehler werden
   manuell mit dem passenden Werkzeug behoben: `e2fsck -f -p` bzw. interaktiv
   (ext2/3/4), `xfs_repair` ohne `-L` (XFS), `btrfs check --repair` nur nach
   Rücksprache / `btrfs scrub` eingehängt (Btrfs), `fsck.fat -a` (vfat), `ntfsfix`
   (NTFS), `fsck.exfat` (exFAT), `zpool scrub` (ZFS). Eine Reparatur ist keine
   Datensicherung.
6. Anschließend in der App erneut prüfen.

Das privilegierte Hilfsprogramm kontrolliert unmittelbar vor dem Start erneut
Geräteidentität, Mounts in eigener/Host-Mount-Namespace und aktive Blockgerät-Layer.
Unlesbare Sicherheitsinformationen führen zum Abbruch. Zusätzlich schützt die
eigene Mount-Prüfung von e2fsck. Fremde Software darf das Gerät dennoch nicht
währenddessen einhängen; die App kann fremde Zugriffe nicht weltweit sperren.
Andere Tests derselben App sind während einer Dateisystemaktion gesperrt.
Die Prüfung wird nicht durch einen Stop-Knopf oder thermischen Wächter beendet;
das normale Schließen der App ist währenddessen gesperrt.

Textprotokoll vollständig, HTML mit einer begrenzten Ausgabevorschau (64.000
Zeichen) und dem Ergebnis. Es gibt keine Reparaturen im Gesamttestplan.
Strukturprüfung (lesend) gibt es für ext2/3/4, XFS, Btrfs, vfat, exFAT (`fsck.exfat -n`), squashfs und VMFS;
NTFS hat eine lesende Basisprüfung (`ntfsfix -n`: Bootsektor, MFT/MFT-Spiegel, Dirty-Flag –
keine vollständige Strukturprüfung, dafür Windows `chkdsk /f`). ZFS hat eine eigene
Pool-Diagnose (siehe oben). Automatische Unmounts und Partitionsänderungen sind nicht enthalten.

## Langzeitmonitoring

- Messintervall 5–3600 Sekunden, Dauer bis 720 Stunden, 0 = bis zum Stoppen.
- Temperaturen und Lüfter über Linux/psutil; RAM-Belegung; ECC-Zähler über EDAC;
  ext4-Fehlerzähler über sysfs; NVIDIA-Temperatur, Leistung, Auslastung und
  unkorrigierbare ECC-Fehler über `nvidia-smi`, sofern verfügbar.
- Neue relevante Kernelmeldungen über `journalctl`: u.a. MCE, PCIe/AER, GPU-Xid,
  I/O- und Dateisystemfehler. Fehlender Journalzugriff wird ausdrücklich vermerkt.
- CSV wird fortlaufend geschrieben. Text/HTML enthalten Min/Max/Mittelwerte und
  höchstens 100 Ereignisdetails; vollständige erfasste Messungen stehen in CSV.
  Bei Logfluten werden pro Abfrage höchstens 500 Journalzeilen ausgewertet;
  eine Begrenzung wird als Ereignis vermerkt. Ausreichend freien Platz einplanen.
- Standardgrenze **100 °C für Sensoren ohne individuellen Grenzwert** ist ein
  editierbarer Startwert, keine Herstellerempfehlung für jedes Bauteil. Besonders
  SSDs benötigen häufig eine niedrigere Grenze. Individuelle Temperaturgrenzen
  lassen sich separat einstellen (siehe oben). Fehlende Sensoren gelten nicht als Hardwarefehler, bedeuten aber fehlende
  Überwachung. Kein Ersatz für Firmware-/Hardware-Temperaturschutz.
- Auf Wunsch stoppt der Wächter bei Grenzüberschreitung, neuen unkorrigierbaren
  ECC-Fehlern oder einem Monitoring-Ausfall die App-Stresstests. Im Monitoring-Tab
  ist das vorbelegt; im Gesamttest gehört der Sicherheitsabbruch zum aktivierten
  Begleitmonitoring. Bereits beim Start vorhandene ECC-Zähler werden als Baseline
  protokolliert, nicht als neu aufgetretene Fehler gezählt.
- Nach einem Sicherheitsstopp bleiben neue Einzeltests gesperrt, bis das
  eigenständige Monitoring beendet wird; die Aufzeichnung läuft bis dahin weiter.
  Das betrifft keine fremden Programme, keine bereits von der NVMe-Firmware
  ausgeführten Selbsttests und keine Dateisystemreparatur.
- Ein Sicherheitsabbruch ist im Gesamttest ein Fehler, kein bestandener Lauf.
  Ein Bericht ohne Ereignisse ist keine vollständige Hardware-Freigabe.
- Berichte verwenden die beim Start gewählte Sprache. Die UI kann unabhängig
  davon jederzeit zwischen Deutsch und Englisch wechseln.

## Installation und Plattformen

Ziel: Ubuntu 24.04 und 26.04 amd64. Das DEB bringt seine Python/Qt-Laufzeit mit;
APT installiert native Werkzeuge einschließlich `e2fsprogs`, `util-linux` und
`pkexec`. Keine Python-venv auf dem Zielsystem nötig. Die neue Reparaturfunktion
wurde mit temporären ext4-Abbildern und simulierten Sicherheitsfällen getestet;
dies ersetzt noch keinen vollständigen Test auf beiden Ubuntu-Versionen.

Beim AppImage kann eine FUSE-Mount-Beschränkung den privilegierten Start verhindern.
Dann das DEB verwenden oder das AppImage als normaler Benutzer mit
`--appimage-extract` entpacken und `squashfs-root/AppRun` starten. Nicht die gesamte
GUI als root starten. Die nativen Werkzeuge müssen auch für ein AppImage vorhanden
sein.

Referenzen: [e2fsck](https://man7.org/linux/man-pages/man8/e2fsck.8.html),
[Ubuntu 26.04 e2fsprogs](https://packages.ubuntu.com/resolute/e2fsprogs),
[Ubuntu 26.04 pkexec](https://packages.ubuntu.com/resolute-updates/amd64/pkexec).

## English summary

The Filesystem tab supports read-only **status diagnosis on mounted filesystems**
(mount options, space, available ext4 counters and recent device-specific kernel
errors). This is not a full integrity check. Offline read-only integrity checks cover
ext2/3/4 (`e2fsck -f -n`), XFS (`xfs_repair -n`), Btrfs (`btrfs check --readonly
--check-data-csum`), vfat (`fsck.fat -n`), exFAT (`fsck.exfat -n`), squashfs and VMFS; NTFS gets a read-only *basic*
check only (`ntfsfix -n`: boot sector, MFT/MFT mirror, dirty flag – a full structure check
requires Windows `chkdsk /f`). **Since 0.2.61 the program
never repairs filesystems**; repairs are a deliberate administrator action on the command
line, and the live image ships the tools (e2fsprogs, xfsprogs, btrfs-progs, dosfstools,
ntfs-3g, exfatprogs, zfsutils-linux, gdisk, testdisk). Mounted filesystems are blocked;
boot a live USB for the running system partition. Device identity and mount state are
revalidated by the privileged worker. Unsupported filesystems are not modified.

Monitoring records CSV incrementally and generates bounded TXT/HTML summaries.
Available temperatures, fans, EDAC, ext4 counters, kernel messages and NVIDIA data
are sampled. Missing sensors/journal access mean incomplete coverage, not hardware
failure. Default 100 °C is an adjustable threshold for **all sensors**, not a
universal safe hardware limit. The optional guard stops this app's stress tests
on temperature limits, new uncorrectable ECC errors or monitoring failure. It
cannot stop firmware self-tests or external programs, and never interrupts a
filesystem repair. HPE Smart Array features are HPE-specific; ordinary PCs do
not require them. Remote iLO/Redfish and per-sensor limits remain future work.
