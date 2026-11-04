"""Hilfe-/Help-Tab mit zweisprachiger Dokumentation aller Tests und Optionen."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from hardwaretest import __version__
from hardwaretest.ui.i18n import language_manager


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
<p style="color:#888;">Version __VERSION__</p>

<h2>Übersicht</h2>
<p>
Diese Anwendung prüft die Stabilität von CPU, RAM, Speichercontroller,
Datenträgern (SATA/SAS/NVMe), Grafikkarten und Netzwerk, liest Dateisysteme,
ZFS-Pools und HPE-ProLiant-Daten aus und überwacht das System langfristig.
Alle Tests laufen unter Linux und nutzen bewährte Werkzeuge wie
<b>stress-ng</b>, <b>Prime95 (mprime)</b>, <b>OpenDCDiag</b>, <b>fio</b>,
<b>nvme-cli</b>, <b>smartctl</b>, <b>ipmitool</b> und <b>iperf3</b>.
</p>
<p>
<b>Welcher Tab wofür?</b> Einzeltests (stress-ng, Prime95, OpenDCDiag,
Speichercontroller, RAM-Fuelltest, Disk, NVMe, GPU, Netzwerk) für gezielte
Fehlersuche; <b>Gesamttest</b> für eine Abnahme mit Bericht; <b>Dateisystem</b>,
<b>ProLiant Info</b>, <b>System</b> und <b>Informationen</b> lesen nur aus;
<b>Langzeitmonitoring</b> begleitet Tests oder den Dauerbetrieb. Alle Tests sind
nicht-destruktiv – mit Ausnahme von <b>Disk (Destruktiv)</b>.
</p>

<h2>Allgemeine Bedienhinweise</h2>
<ul>
  <li><b>Berichte:</b> Jeder Tab schreibt nach jedem Test automatisch einen HTML-Bericht
      nach <code>~/Downloads/hwtest-&lt;test&gt;-JJJJMMTT-HHMMSS.html</code> (z.&nbsp;B.
      <code>hwtest-stress-ng-…</code>, <code>hwtest-gesamttest-…</code>). Alle Berichte haben
      den gleichen Aufbau: Status, Zusammenfassung, Parameter, Abschnitte, Hinweise und
      Rohausgabe. „Bericht öffnen“ zeigt den zuletzt erzeugten Bericht; der Gesamttest
      legt zusätzlich eine CSV mit gleichem Namen ab.</li>
  <li><b>Dauer:</b> Stunden / Minuten / Sekunden – die Gesamtlaufzeit des Tests.</li>
  <li><b>CPU-Worker (Threads):</b> Anzahl der Test-Worker auf logischen CPUs,
      nicht die Anzahl physischer Kerne. Beispiel: 32 physische Kerne mit
      Hyperthreading ergeben 64 logische CPUs. Die GUI-Reserve ist ein Minimum
      in logischen CPUs; ganze physische Kerne samt HT-Partnern bleiben frei.
      Bei 64 logischen CPUs und einer Reserve von 1 sind deshalb normalerweise
      62 Test-Threads verfügbar. Die tatsächliche Reserve wird oben angezeigt.</li>
  <li><b>Swap deaktivieren:</b> Für RAM-Tests unbedingt empfohlen, damit der
      physische Speicher getestet wird und nicht auf die Festplatte ausgewichen wird.</li>
  <li><b>Temperatur-Monitor:</b> Zeigt pro CPU-Socket eine Zusammenfassung
      (Min/Ø/Max). Bei Dual-Socket-Systemen mit vielen Cores wird die Anzeige
      automatisch kompakt. Die Detailansicht zeigt so viele Cores pro Seite, wie
      Platz ist (höchstens 24); weitere Cores sind über „Vorherige/Nächste“
      erreichbar. Gleiche Core-Namen auf mehreren Sockets erhalten ein Präfix
      (S0, S1).<br><i>Core-Nummern:</i> Die Nummern stammen vom Prozessor selbst
      (Hardware-Core-ID, Treiber <code>coretemp</code>), nicht von der App. Sie
      sind daher oft lückenhaft und unsortiert. Beispiel Intel-Hybrid-CPU mit
      6 P- und 4 E-Cores: P-Cores heißen Core 0, 4, 8, 12, 16, 20, E-Cores
      Core 24–27. Hyperthreading-Threads teilen sich einen Core und Sensor,
      deshalb gibt es weniger Einträge als logische CPUs.</li>
  <li><b>ECC/EDAC:</b> Zeigt korrigierbare und unkorrigierbare Speicherfehler an
      (nur bei ECC-RAM und geladenem EDAC-Kernelmodul).</li>
  <li><b>Pass/Fail:</b> Nach dem Test wird automatisch anhand der Ausgabe und des
      Exit-Codes bewertet, ob Fehler aufgetreten sind.</li>
  <li><b>Hardwarefehler (rasdaemon):</b> Ist <code>rasdaemon</code> installiert
      (im Live-ISO immer), wertet jeder Test zusätzlich die Hardwarefehler aus, die
      während des Tests neu protokolliert wurden: Machine Checks der CPU (Cache,
      Bus, Speichercontroller), EDAC-Speicherfehler, PCIe-AER und Speicherseiten,
      die der Kernel aussortiert hat. Ein <b>unkorrigierbarer</b> Fehler (UE) lässt
      den Test fehlschlagen. <b>Korrigierte</b> Fehler (CE) sind nur eine Warnung
      im Protokoll und im Bericht des Gesamttests – sie kommen gelegentlich vor,
      eine Häufung deutet aber auf ein schwaches Modul oder einen schwachen Kern.
      Hinweis: Korrigierte CPU-Fehler meldet der Kernel teils erst nach bis zu
      5 Minuten; nach sehr kurzen Tests fehlen sie dann noch. Alle Einträge:
      <code>sudo ras-mc-ctl --errors</code>.</li>
  <li><b>Hilfsknöpfe:</b> „Systemwerte aktualisieren“ liest freien RAM, Swap und
      CPU-Anzahl neu ein. „btop starten“ öffnet den Systemmonitor, „Kernel-Logs“
      und „MCE-Logs“ zeigen <code>dmesg</code> bzw. Machine-Check-Einträge – nach
      jedem Test einmal ansehen.</li>
  <li><b>Root-Rechte:</b> Gerätetests, NVMe, Dateisystem, ProLiant Info und Teile
      von OpenDCDiag/System fragen das Administratorpasswort ab. Auf dem Live-ISO
      entfällt die Abfrage meist; im .deb erlauben sudoers-Regeln einzelne Befehle
      ohne Passwort.</li>
  <li><b>Gleichzeitigkeit:</b> Immer nur einen Stresstest laufen lassen – parallele
      Tests verfälschen Temperaturen, Durchsatz und die Fehlerzuordnung. Nur das
      Langzeitmonitoring ist als Begleiter gedacht.</li>
</ul>

<hr>

<h2>Tab: stress-ng</h2>
<p>
Flexibler Stresstest für CPU und RAM mit verschiedenen Modi:
</p>
<table>
  <tr><th>Modus</th><th>Beschreibung</th><th>Einsatz</th></tr>
  <tr>
    <td><b>CPU-Last (maximale Hitze)</b></td>
    <td>Maximale CPU-Belastung (Matrix, alle Methoden)</td>
    <td>CPU-Stabilität, Kühlung testen</td>
  </tr>
  <tr>
    <td><b>CPU-Mischlast mit Ergebnisprüfung</b></td>
    <td>Ganzzahl, Gleitkomma, FMA, Vektor, Matrix, L1-Cache und Kompression
        gleichzeitig, jede Lastart mit <code>--verify</code></td>
    <td>Rechenfehler einzelner Einheiten finden</td>
  </tr>
  <tr>
    <td><b>RAM-Test (Fehler finden)</b></td>
    <td>Aggressiver Speichertest mit <code>--verify</code></td>
    <td>RAM-Fehler finden</td>
  </tr>
  <tr>
    <td><b>RAM-Bandbreite (Durchsatz)</b></td>
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
<b>RAM (MB):</b> Gesamte Speichermenge für den Test; sie wird gleichmäßig auf die
Worker verteilt. Bei RAM-Tests möglichst den gesamten freien Speicher nutzen, aber
Reserve für OS und GUI lassen (ohne Swap friert das System sonst ein).<br>
<b>CPU-Worker / GUI-Reserve:</b> siehe „Allgemeine Bedienhinweise“. Die Reserve hält
die Oberfläche bedienbar; für maximale Hitze Reserve 1, für lange Läufe 2.<br>
<b>Swap deaktivieren / aktivieren:</b> schaltet Swap systemweit ab bzw. wieder an
(Root). „memtest86+ pruefen“ zeigt, ob memtest86+ im Boot-Menü verfügbar ist.<br>
<b>Sinnvoll:</b> schneller Erst-Check (10–30 Min.) und Kühlungstest; RAM-Test als
Ergänzung zum RAM-Fuelltest. <b>Nicht geeignet</b> als alleiniger RAM-Nachweis –
dafür RAM-Fuelltest und memtest86+ (außerhalb des Betriebssystems) verwenden.
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
<b>Worker Threads:</b> Standard = logische CPUs minus GUI-Reserve.<br>
<b>Direktmodus</b> („Nur mprime -t“): Nutzt die bestehende <code>prime.txt</code>/<code>local.txt</code>
ohne sie zu überschreiben – nur für eigene, handgepflegte Konfigurationen.<br>
<b>Worauf achten:</b> Prime95 meldet Fehler erst nach vollständigen Durchläufen;
unter 1 Stunde ist die Aussage schwach, 4+ Stunden Blend gelten als belastbar.
Kühlung beobachten – Small FFTs treiben die CPU an die Temperaturgrenze.
<b>Sinnvoll</b> für CPU, Spannungsversorgung und Speicher-Timings;
<b>nicht sinnvoll</b> für Datenträger oder GPU.
</p>

<hr>

<h2>Tab: OpenDCDiag</h2>
<p>
<b>OpenDCDiag</b> (Intel, Apache 2.0) sucht <b>stille Datenfehler</b>: Kerne, die unter Last
falsch rechnen, ohne abzustürzen oder einen Machine Check zu melden. Jeder Kern rechnet
Aufgaben mit bekanntem Ergebnis (Eigen-Matrizen, zlib/zstd-Kompression, Intel In-Field Scan)
und vergleicht. Läuft auf Intel und AMD.
</p>
<ul>
  <li><b>Ergebnis:</b> Fehlerhafte Kerne werden mit Sockel, Kern und logischer CPU genannt,
      z. B. <code>S1 Core 3 (CPU 35)</code>. Ein einziger Fehler bedeutet: Prozessor tauschen.</li>
  <li><b>CPU-Auswahl:</b> „Alle CPUs“ (Standard), „Nur Sockel N“ je erkanntem Sockel
      oder „Benutzerdefiniert …“ – dann wird das Textfeld frei für die
      <code>--cpuset</code>-Schreibweise von OpenDCDiag: logische CPUs/Threads, z. B.
      <code>0-7,16</code>; <code>p1</code> = Sockel 1; <code>c3</code> = Kern 3.
      Sinnvoll, um einen verdächtigen Sockel oder Kern gezielt nachzutesten.</li>
  <li><b>Variante:</b> Auf CPUs ohne AVX2 (z. B. ProLiant Gen8) wird automatisch die
      generische Variante gewählt.</li>
  <li><b>Rechte:</b> Ohne root werden Tests mit MSR-Zugriff und In-Field Scan übersprungen
      (im Protokoll aufgeführt). Im Gesamttest läuft OpenDCDiag über die Root-Sitzung. Das .deb erlaubt Mitgliedern der
      Gruppe sudo, OpenDCDiag ohne Passwort als root zu starten
      (/etc/sudoers.d/hardwaretest-opendcdiag); der Tab nutzt das automatisch.</li>
  <li><b>Dauer:</b> 10 Minuten als Schnelltest, für Abnahmen 1 Stunde oder mehr.</li>
  <li><b>Worauf achten:</b> OpenDCDiag ergänzt Prime95/stress-ng, ersetzt sie aber nicht –
      es findet Rechenfehler je Kern, keine Kühlungs- oder RAM-Probleme. Ein Fehler ist
      reproduzierbar? Dann mit „Nur Sockel N“ eingrenzen und den Prozessor tauschen.</li>
</ul>

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
  <li><b>Worauf achten:</b> Der Test erzeugt wenig Hitze, aber hohe Bus-Last;
      Fehler zeigen sich meist als Machine Check oder EDAC-Eintrag, nicht als Absturz –
      deshalb nach dem Lauf „MCE-Logs“ und die EDAC-Anzeige prüfen.
      <b>Sinnvoll</b> bei Mehrsockel-Systemen, nach RAM-/CPU-Tausch und bei
      sporadischen Abstürzen ohne Befund in Prime95; <b>wenig sinnvoll</b> auf
      Einzelsockel-Systemen als Erst-Check – dort reichen stress-ng und RAM-Fuelltest.</li>
</ul>

<hr>

<h2>Tab: RAM-Fuelltest</h2>
<p>Belegt in jedem Zyklus fast den gesamten freien RAM, schreibt ein Bitmuster
(parallel auf allen Kernen bis auf zwei), verifiziert es, gibt den Speicher frei
und wiederholt. Die Blöcke wechseln je Zyklus die CPU.</p>

<h3>Ablauf eines Zyklus</h3>
<ol>
<li><b>Allokieren:</b> verfügbarer RAM minus Reserve, in 256-MB-Blöcken.</li>
<li><b>Schreiben:</b> ein Muster pro Zyklus, jeder Prozess fest an eine CPU gebunden.
Zwei Kerne bleiben für OS/GUI frei; welche das sind, wechselt jeden Zyklus, sodass
über mehrere Zyklen alle Kerne testen.</li>
<li><b>Sperren (mlock):</b> der beschriebene Speicher wird gegen Auslagern
gesperrt, damit wirklich RAM und nicht Swap geprüft wird.</li>
<li><b>Verifizieren:</b> durch <i>andere</i> CPUs als beim Schreiben (auf
Mehrsockel-Systemen damit auch über den anderen Speichercontroller).</li>
<li><b>Moving Inversion</b> (wenn die Restzeit reicht): erneut
lesen und vergleichen, invertiert schreiben, das Invertierte prüfen. Findet Bits,
die mit der Zeit kippen, und Kopplungen beim Umschalten aller Bits.</li>
<li><b>Freigeben</b> und kurze Pause (Speicherrückgang in btop sichtbar). Der Testspeicher
ist Shmem (in btop „Cached“) und wird per MADV_REMOVE sofort ausgestanzt. Bei sehr
großem RAM (TB-Bereich) braucht der Kernel einige Zeit, bis der Speicher wieder
als verfügbar gilt; der Test wartet darauf (ca. 1 s je 8 GB, max. 10 min) und
protokolliert die Wartezeit.</li>
</ol>

<h3>Muster</h3>
<ul>
<li>0xAA, 0x55, 0xFF, 0x00 – hängende Bits.</li>
<li>Walking-One/Zero je Byte – Bit-Verwechslungen.</li>
<li>Zufällige 64-Bit-Wörter und Zufallsdaten (wechseln jede Runde).</li>
<li>Address-in-Data und invertiert – Fehler der Adressdecodierung.</li>
<li><b>Datenleitungstest</b> (einmal je Musterrunde): 64-Bit
Walking-One/Zero über 128 MB. Meldet die betroffenen Datenbits.</li>
</ul>

<h3>Optionen</h3>
<ul>
<li><b>Speicher:</b> „automatisch“ (0) = gesamter verfügbarer RAM abzüglich Reserve;
ein fester Wert belegt genau diese Menge je Zyklus.</li>
<li><b>Reserve (OS/GUI):</b> bleibt bei automatischer Speichermenge frei
(Standard 512 MB; der Test erzwingt mindestens 1 % des RAM).</li>
<li><b>Blockgroesse:</b> Größe der Einzelblöcke (Standard 256 MB). Kleinere Blöcke
helfen bei fragmentiertem Speicher, größere sind schneller.</li>
<li><b>Pause je Zyklus:</b> Wartezeit mit freigegebenem Speicher (Standard 3 s),
sodass der Rückgang z. B. in btop sichtbar ist. 0 = sofort weiter.</li>
<li><b>Swap deaktivieren</b> vor dem Start, sonst wird Testspeicher ausgelagert
und die Aussage verwässert.</li>
</ul>

<h3>Schutz des Systems</h3>
<ul>
<li><b>Reserve (OS/GUI):</b> bleibt frei; der Test erzwingt mindestens 1 % des RAM
(1–8 GB).</li>
<li><b>Speicherdruck:</b> fällt der freie RAM während des Tests unter die halbe
Reserve (z.&nbsp;B. weil ein anderes Programm wächst), werden Blöcke vom Ende her
verworfen und nicht geprüft. Das steht im Protokoll.</li>
<li><b>OOM-Schutz:</b> wird der Speicher trotzdem knapp, beendet der Kernel zuerst
den Test, nicht OS oder GUI.</li>
<li class="bad"><b>Speicher manuell setzen:</b> Unbedingt genug freien Speicher für das
Betriebssystem einplanen (empfohlen: mindestens 2–4 GB bzw. 1–2 % des RAM).
Ohne Swap kann der Testspeicher nicht ausgelagert werden – ist er zu groß,
friert das System ein.</li>
</ul>

<h3>Fehlermeldungen</h3>
<p><code>MISMATCH bei Offset …</code> nennt die Position im Testspeicher, erwarteten
und gelesenen Wert. Als root wird zusätzlich die <b>physische Adresse</b>
angegeben; damit lässt sich das Modul (DIMM) über die Herstellerunterlagen bzw.
<code>edac-util</code> eingrenzen. Ohne root fehlt diese Angabe.</p>

<h3>Grenzen</h3>
<ul>
<li>Speicher von Kernel, Treibern und laufenden Programmen sowie die Reserve
werden <b>nie</b> getestet.</li>
<li>Der Test läuft unter einem laufenden Betriebssystem. Die Prüfung erfolgt
seitenweise über virtuelle Adressen; welche physischen Adressen getroffen werden,
bestimmt der Kernel. Lückenlos ist das nicht.</li>
<li>Ohne root ist die Sperre gegen Auslagern auf das Limit
(<code>ulimit -l</code>) begrenzt; mit aktivem Swap kann dann ein Teil
ausgelagert werden. Es erscheint eine Warnung.</li>
<li>ECC-Speicher korrigiert Einzelbitfehler selbst; diese sieht der Test nicht,
sondern nur das Systemprotokoll bzw. EDAC/iLO. Der Test findet nicht
korrigierbare oder nicht abgesicherte Fehler.</li>
<li>Python erreicht etwa 2–4 GB/s je Kern. Bei sehr viel RAM passt ein Zyklus
eventuell nicht in die Testzeit; dann wird nur der geschriebene Teil geprüft.</li>
<li>Für eine vollständige Prüfung (inkl. Hammer- und Bitfade-Tests)
MemTest86 im Boot-Menü verwenden.</li>
</ul>

<h2>Tab: Disk (Datei / Geräte / Destruktiv)</h2>
<p>
Festplatten-/SSD-Tests mittels <b>fio</b> – optimiert für HPE ProLiant Server
mit SmartArray RAID-Controllern, SAS-/NVMe-Laufwerken.
</p>

<h3>Datei-Test (sicher)</h3>
<ul>
  <li>Liest/schreibt in einer temporären Datei auf einem beliebigen Dateisystem
      („Testdatei“ über „Pfad wählen“ festlegen).</li>
  <li>Kein Root-Zugriff nötig – sicher auf Produktivsystemen.</li>
  <li>Workload wählbar: <code>read</code>, <code>write</code>, <code>randread</code>,
      <code>randwrite</code>, <code>readwrite</code>, <code>randrw</code>.</li>
  <li><b>Blockgröße</b> (Standard 128k; 4k für IOPS, 1M für Durchsatz), <b>Dateigröße</b>
      (Standard 4096 MB – deutlich größer als RAM und Laufwerks-Cache wählen, sonst wird nur
      der Cache gemessen), <b>iodepth</b> (32) und <b>Jobs</b> (parallele fio-Prozesse).</li>
  <li>Bei Schreib-Workloads wird automatisch <b>CRC32c-Verifikation</b> aktiviert
      (<code>--verify=crc32c --do_verify=1 --verify_fatal=1</code>).</li>
  <li><span class="warn">Live-ISO:</span> Liegt der Zielordner im Arbeitsspeicher
      (overlay, tmpfs, ramfs, squashfs), verweigert der Test den Start – er würde nur den
      RAM messen. Einen eingehängten Datenträger unter <code>/media</code> oder
      <code>/mnt</code> wählen.</li>
  <li>Geeignet für: Dateisystem-Integritätstests, SSD-Dauerlauf, IOPS-Benchmarks auf
      Laufwerken mit Daten. <b>Nicht geeignet</b>, um defekte Sektoren zu finden – dafür
      den Geräte-Test nutzen, der jeden Block liest.</li>
</ul>

<h3>Geräte-Test (nur lesen)</h3>
<ul>
  <li>„Rohgeräte-Read starten“ liest alle Blöcke direkt vom Blockgerät via <code>fio</code>
      mit Root-Rechten (sudo-Passwortdialog).</li>
  <li>Erkennt defekte Sektoren, SCSI-Fehler und Medium-Errors – besonders wichtig
      nach RAID-Rebuilds auf HPE SmartArray.</li>
  <li>Nutzt <code>continue_on_error=read</code>: Liest alle Blöcke weiter, auch
      wenn einzelne fehlschlagen → komplettes Fehlerbild.</li>
  <li>Keine Datenänderung – sicher auf bestehenden Systemen. Eingehängte Laufwerke
      bleiben auswählbar. Es gibt keine Zeitvorgabe: Der Lauf endet, wenn alle Blöcke
      gelesen sind (mehrere TB = Stunden); Fortschritt und Durchsatz werden angezeigt.</li>
</ul>

<h3>Destruktiver Test <span class="bad">(⚠ DATENVERLUST!)</span></h3>
<ul>
  <li>„DESTRUKTIVEN Test starten“ überschreibt <b>alle Daten</b> auf den gewählten
      Geräten und verifiziert anschließend per CRC32c-Rücklesen.</li>
  <li><code>verify_backlog=16384</code> / <code>verify_backlog_batch=4096</code> –
      optimiert für große RAID-Volumes (mehrere TB).</li>
  <li>„Schreib-/Lesedurchläufe“ 1–10 für intensiven Dauertest.</li>
  <li><span class="bad">Nur für leere/neue Datenträger verwenden!</span> Partitionstabelle
      und Dateisysteme sind danach weg. Sinnvoll bei Neuware, vor Inbetriebnahme von
      RAID-Mitgliedern und zum Nachweis bei Reklamationen; bei SSDs kostet jeder
      Durchlauf Schreibvolumen (TBW).</li>
</ul>

<h3>Datenträgerauswahl &amp; Aushängen</h3>
<ul>
  <li>Laufwerke einzeln über das Häkchen auswählen; „Alle auswählen“ / „Alle abwählen“
      wirken nur auf auswählbare Einträge.</li>
  <li>Im Tab „Destruktiv“ ist anfangs <b>kein</b> Laufwerk ausgewählt.</li>
  <li>Eingehängte oder belegte Laufwerke (Mounts, Swap, LVM/LUKS, mdraid, ZFS) sind
      dort mit „belegt“ markiert und <b>gesperrt</b>. Im Tab „Geräte“ (nur lesen)
      bleiben sie auswählbar.</li>
  <li><b>Datenträger aushängen…</b>: hängt alle Partitionen eines gewählten Laufwerks
      per <code>udisksctl</code> aus (Fallback <code>umount</code> als root) und scannt neu.
      Der Systemdatenträger (<code>/</code>, <code>/boot</code>, <code>/home</code>, Swap …)
      wird nicht angeboten.</li>
  <li>Unmittelbar vor dem Start wird erneut geprüft, ob die Laufwerke frei und
      unverändert (Seriennummer/WWN) sind.</li>
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
  <li><b>RAID-Controller-Status</b>: Springt zur strukturierten Auswertung der
      HPE-Smart-Array-/MegaRAID-Controller im Tab „ProLiant Info“ → „RAID“.</li>
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

<h2>Tab: NVMe</h2>
<p>
Diagnose und nicht-destruktive Tests für NVMe-Laufwerke mit <code>nvme-cli</code>
(Root-Rechte, einmalige Passwortabfrage) und <code>fio</code>.
</p>
<ul>
  <li><b>NVMe-Laufwerke suchen</b> listet alle Namespaces; das Laufwerk für
      Diagnose/Selbsttest wird in der Auswahlliste gewählt.</li>
  <li><b>SMART-/Gesundheitsdaten lesen:</b> kritische Warnungen, Temperatur, verbrauchte
      Lebensdauer (%), Medien- und Fehlerprotokoll-Einträge, gelesene/geschriebene Daten,
      Einschaltzyklen und unerwartete Stromausfälle. <span class="warn">Achten auf:</span>
      <i>critical warning</i> ≠ 0, <i>percentage used</i> ≥ 90 %, steigende
      <i>media errors</i> oder <i>unsafe shutdowns</i>.</li>
  <li><b>Selbsttest:</b> „Kurzer NVMe-Selbsttest“ (wenige Minuten) oder „Erweiterter
      NVMe-Selbsttest“ (je nach Laufwerk bis zu Stunden) laufen in der Firmware des
      Laufwerks; der Fortschritt wird angezeigt. „Selbsttest-Protokoll lesen“ zeigt die
      letzten Ergebnisse. Während eines Selbsttests ist kein Lesetest möglich.</li>
  <li><b>NVMe-Lesetests</b> (nur lesend, ein oder mehrere Ziellaufwerke): der
      <b>Lese-Benchmark</b> misst Durchsatz sequenziell oder zufällig über die eingestellte
      Dauer (Standard 30 s); der <b>vollständige Lese-Test</b> liest jeden Block aller
      gewählten Namespaces parallel – findet unlesbare Blöcke, kann aber viele Stunden
      dauern. „Lese-Test stoppen“ bricht ab.</li>
  <li><b>Sinnvoll:</b> SMART vor und nach jedem Stresstest vergleichen; erweiterter
      Selbsttest und vollständiger Lesetest bei Verdacht auf Lesefehler oder vor
      Inbetriebnahme. <b>Nicht sinnvoll:</b> Benchmarks auf einem Laufwerk, das gerade
      vom System genutzt wird – die Werte sind dann nicht vergleichbar. Hohe Temperaturen
      (> 70 °C) während des Lesetests deuten auf fehlende Kühlung des M.2-Steckplatzes.</li>
</ul>

<hr>

<h2>Tab: Netzwerk</h2>
<p>
Der Tab zeigt alle erkannten Netzwerkadapter mit Verbindungsstatus, Treiber,
PCI-Adresse, Linkgeschwindigkeit, Duplex, MTU, MAC sowie IPv4/IPv6-Adressen.
Mit „Automatisch (Routing)“ bestimmt Linux den Übertragungsweg. Wird ein Adapter
ausgewählt, bindet Hardwaretest Ping und iperf3 gezielt an diese Schnittstelle.
</p>
<ul>
  <li><b>Ping</b> misst Paketverlust, minimale/mittlere/maximale Latenz und Jitter.</li>
  <li><b>iperf3</b> misst TCP-Durchsatz und Retransmits; auf dem Ziel muss
      <code>iperf3 -s</code> laufen.</li>
  <li>Grenzwerte bestimmen unabhängig vom Programm-Exitcode, ob der Test bestanden ist
      (Vorgaben: Paketverlust 0 %, mittlere Latenz 100 ms, Jitter 20 ms, Mindestdurchsatz
      0 = aus). Null-Grenzwerte außer dem Durchsatz sind strikt.</li>
  <li><b>Worauf achten:</b> Das Ziel muss im selben Netz erreichbar sein und Ping
      zulassen; ohne laufenden <code>iperf3 -s</code> schlägt der Durchsatztest fehl –
      das ist kein Hardwarefehler. Für aussagekräftige Durchsätze ein Ziel mit gleicher
      oder höherer Linkgeschwindigkeit wählen. „Netzwerkadapter aktualisieren“ nach dem
      Umstecken von Kabeln. <b>Sinnvoll</b> für Linkstabilität, Kabel/Transceiver und
      Treiberprobleme; <b>nicht sinnvoll</b> als Messung über Internet oder WLAN.</li>
  <li><b>ethtool-Selbsttest</b> (<code>ethtool -t</code>, Root-Rechte, nur physische
      Adapter): Der Treiber prüft Register, EEPROM, Interrupts, Loopback und Link.
      „Offline (gründlich, trennt den Link kurz)“ testet mehr, unterbricht aber
      laufende Verbindungen. Nicht jeder Treiber unterstützt den Test („Operation not
      supported“ ist kein Hardwarefehler). Vor und nach dem Test werden die Fehlerzähler
      aus <code>ethtool -S</code> (CRC, rx/tx errors, dropped) verglichen; ein Zuwachs
      wird im Ergebnis als Warnung angezeigt. Meldet der Treiber FAIL, obwohl alle
      Einzeltests 0 sind, konnte der Test nicht laufen (gelb, „nicht aussagekräftig“):
      <code>bnxt_en</code> (Broadcom NetXtreme‑E, z. B. HPE 25G) verweigert den Offline‑Test
      auf NPAR‑/geteilten PFs oder mit aktiven VFs, ebenso bei nicht aktiver Schnittstelle –
      der Grund steht in <code>dmesg</code>. Dann den Online‑Test verwenden.</li>
  <li><b>SFP-Modul lesen</b> (<code>ethtool -m</code>): Hersteller, Typ, Seriennummer,
      Temperatur, Spannung sowie Rx-/Tx-Leistung des Transceivers. Bei RJ45-Adaptern
      ohne Modul erscheint ein Hinweis.</li>
  <li><b>Mellanox Link-Diagnose</b> (<code>mstlink</code> aus dem freien Paket
      <code>mstflint</code>, Root): nur sichtbar bei Mellanox/NVIDIA-Adaptern (mlx4/mlx5),
      wenn <code>mstflint</code> installiert ist. Zeigt Link-Zustand, Geschwindigkeit, FEC,
      Firmware-Empfehlung („No issue was observed“), physische Fehler/BER, Link-Down-Zähler
      und die Moduldaten mit Grenzwerten. <span style="color:#ef6c00">Orange</span> bei
      Fehlerzählern &gt; 0, Werten außerhalb der Modulgrenzen oder einer anderen Empfehlung;
      <span style="color:#c62828">rot</span> ohne Link. Alle Werte stehen im Log.</li>
</ul>

<hr>

<h2>Tab: NVIDIA GPU</h2>
<p>
Für Inventar- und Gesundheitsdaten muss ein geeigneter NVIDIA-Treiber installiert
sein und <code>nvidia-smi</code> funktionieren. Aktive Tests benötigen zusätzlich
NVIDIA DCGM 4 mit dem zur Treiberversion passenden Paket
<code>datacenter-gpu-manager-4-cuda12</code> oder <code>-cuda13</code> sowie den
gestarteten Dienst <code>nvidia-dcgm</code>. CUDA Toolkit, PyTorch und Docker sind
nicht erforderlich. DCGM-Stufe 1 ist ein kurzer Bereitschaftstest; die Stufen 2–4
prüfen zunehmend Speicher, PCIe, Rechenlast und weitere Hardwarefunktionen – Stufe 3
(ca. 30 min) bzw. 4 (länger) führt alle DCGM-Tests nacheinander aus (Gesamttest der GPU).
Das Inventar zeigt außerdem aktuelle GPU-/SM-/Speichertakte sowie die aktuelle
und maximal mögliche PCIe-Generation und Linkbreite. Die Liveüberwachung liest
im gewählten Intervall Auslastung, Temperatur, VRAM, Leistungsaufnahme und Takte
der markierten GPUs. Beim Start einer DCGM-Diagnose wird sie automatisch aktiviert.
Der <b>GPU-Lasttest</b> funktioniert ohne DCGM auf jeder NVIDIA-GPU ab Turing
(GeForce GTX 16xx/RTX 20xx–50xx, RTX/Quadro, Rechenzentrums-GPUs). Er benötigt nur
den NVIDIA-Treiber (libcuda.so.1), erzeugt Rechenlast mit Ergebnisvergleich und
prüft den eingestellten Anteil des freien VRAM fortlaufend mit Bitmustern.
Rechen- oder Speicherfehler führen zu „FEHLGESCHLAGEN“. Ein CUDA-Toolkit ist nicht nötig.
<br><b>Unterstützte NVIDIA-GPUs</b> (Treiber 595, Open-Kernelmodul, ab Turing):
GeForce GTX 16xx und RTX 20xx/30xx/40xx/50xx; RTX/Quadro ab Turing;
Rechenzentrum: Tesla T4, Ampere A2/A10/A16/A30/A40/A100, Ada L4/L40/L40S,
RTX 2000–6000 Ada, Hopper H100/H200, Blackwell B200/RTX PRO.
DCGM-Diagnosen sind für Rechenzentrums-GPUs vorgesehen; bei Workstation-Karten
laufen nur Teile, GeForce-Karten werden nicht unterstützt.
Ist <code>dcgmi</code> nicht installiert, wird der Bereich „DCGM-Diagnose“
ausgeblendet (ebenso die DCGM-Option im Gesamttest).
Nach jeder DCGM-Diagnose wird ein <b>HTML-Bericht</b> in <code>~/Downloads</code>
(<code>dcgm-diagnose-DATUM.html</code>) abgelegt: Gesamtergebnis, Datenblatt aller
geprüften GPUs (Modell, Seriennummer, UUID, VBIOS, PCIe-Generation/-Breite, ECC,
Retired/Remapped Pages, Leistungsgrenzen, Takte, Temperatur) sowie die Ergebnistabelle
pro Test und GPU. „HTML-Bericht öffnen“ zeigt ihn im Browser. „GPU-Datenblatt (HTML)“
erzeugt dasselbe Datenblatt ohne Diagnose (<code>gpu-datenblatt-DATUM.html</code>).
Schlägt bei zwei oder mehr GPUs nur der <b>pcie</b>-Test mit „P2P write verification
failed: buffer mismatch“ (ID 86/87) fehl, ist das kein GPU-Defekt: Der Peer-to-Peer-Teil
kopiert direkt zwischen zwei GPUs und scheitert bei aktivem IOMMU/VT-d mit PCIe-ACS oder
GPUs an verschiedenen CPU-Sockeln. Abhilfe: „PCIe-Peer-to-Peer-Test überspringen“
ankreuzen (standardmäßig aus) (<code>pcie.test_p2p_on=false</code>).
Soll P2P wirklich funktionieren, gibt es drei Stellschrauben: (1) Kernelparameter
<code>iommu=pt</code> (notfalls <code>intel_iommu=off</code> bzw. <code>amd_iommu=off</code>) in <code>GRUB_CMDLINE_LINUX_DEFAULT</code>,
dann <code>update-grub</code> und Neustart; (2) im BIOS/RBSU unter „Advanced Options“
PCIe Access Control Services (ACS) bzw. VT-d/IOMMU deaktivieren, sofern kein SR-IOV
benötigt wird; (3) Treiber-Override <code>options nvidia NVreg_RegistryDwords="ForceP2P=1"</code>
in <code>/etc/modprobe.d/nvidia.conf</code> (ein vorhandenes <code>ForceP2P=0</code> ersetzen),
danach Neustart. Auf Synergy-Blades sitzen die GPUs im Grafik-Erweiterungsmodul hinter
PCIe-Switches und Mezzanine-Karten – dort bleibt das Überspringen meist die praktikable Lösung.
Der Bericht zeigt bei DCGM 4 neben den
GPU-Einzelergebnissen auch den Gesamtstatus je Test („Gesamt“), wenn er schlechter ausfällt.
Nicht unterstützt: Pascal/Volta (GTX 10xx, P4, P40, P100, V100) und älter.
Die vollständige Installationsanleitung liegt unter
<code>/opt/hardwaretest/docs/NVIDIA_GPU.md</code>.
</p>
<p>
<b>Bedienung:</b> „NVIDIA-GPUs suchen“ erstellt das Inventar, „Linux-Basisprüfung“
prüft ohne Treiberzwang PCIe-Geräte, gebundene Treiber, Kernelmodule und Geräteknoten
(sinnvoll, wenn <code>nvidia-smi</code> fehlt). GPUs für Tests per Häkchen wählen.
<b>GPU-Lasttest:</b> Dauer (Standard 10 min) und „Anteil des freien VRAM“ (80 %) –
bei Karten, die gleichzeitig den Desktop anzeigen, eher 50–60 % wählen.
<b>Worauf achten:</b> Taktabfall bei steigender Temperatur deutet auf Throttling
(Kühlung/Lüfterkurve), Fehler im VRAM-Muster auf defekten Speicher, Abbrüche mit
Xid-Fehlern in den Kernel-Logs auf Treiber-/Stromversorgungsprobleme. Für eine
Abnahme von Rechenzentrums-GPUs DCGM-Stufe 3 oder 4 verwenden (Stunden); Stufe 1
ist nur ein Bereitschaftstest.
</p>

<hr>

<h2>Tab: GPU (Vulkan)</h2>
<p>
Herstellerneutraler Lasttest für Grafikkarten von <b>AMD</b> (Radeon, Radeon Pro,
Instinct), <b>Intel</b> (UHD/Iris Xe, Arc) und <b>NVIDIA</b> (über NVK oder den
proprietären Treiber) mit Vulkan Compute. Benötigt werden nur der Vulkan-Loader
(<code>libvulkan1</code>) und ein Vulkan-Treiber, z. B. <code>mesa-vulkan-drivers</code>
(RADV, ANV, NVK). Software-Renderer (llvmpipe) werden ausgeblendet.
Der Test füllt den eingestellten Anteil des freien Grafikspeichers mit wechselnden
Bitmustern, prüft sie auf der GPU (Fehler werden per Zähler erfasst) und erzeugt
dazwischen Rechenlast mit Ergebnisvergleich aller Threads. Rechen- oder
Speicherfehler führen zu „FEHLGESCHLAGEN“. Bei <b>integrierten GPUs</b> werden
höchstens 25 % des gemeinsamen Speichers belegt, damit das System bedienbar bleibt.
Die Liveüberwachung liest ohne Zusatzprogramme aus sysfs: Temperatur, Leistung,
Auslastung, GPU-/Speichertakt, VRAM und Lüfter (Umfang je nach Treiber: amdgpu
liefert fast alles, i915/xe vor allem den Takt; bei integrierten Intel-GPUs
zusätzlich die Temperatur des CPU-Pakets). Bei integrierten Intel-GPUs ermittelt
die App die Auslastung aus <code>/proc/*/fdinfo</code> (wie <code>intel_gpu_top</code>/
<code>gputop</code>; ohne root nur für Prozesse des eigenen Benutzers, also auch den
Lasttest) und die Leistung aus dem RAPL-Zähler „uncore“ (Zusatz „(RAPL)“). Diese
Zähler sind normalerweise nur für root lesbar („Leistung nur mit root“); im
HPE-Live-ISO gibt eine udev-Regel sie frei. Takt als aktuell/maximal, im
Ruhezustand 0 MHz. Die erste Zeile zeigt die Uhrzeit der letzten Aktualisierung.
Server-Grafikchips (Matrox, ASPEED) haben keinen Vulkan-Treiber und erscheinen
nur als „ohne Vulkan“. Für NVIDIA-Karten mit proprietärem Treiber bietet der Tab
„NVIDIA GPU“ zusätzlich den CUDA-Lasttest und DCGM-Diagnosen.
</p>
<p>
<b>Bedienung:</b> „GPUs suchen“, gewünschte Karten ankreuzen, Dauer (Standard 10 min)
und „Anteil des freien Grafikspeichers“ (80 %) einstellen, „GPU-Lasttest starten“.
<b>Worauf achten:</b> Der Desktop kann während des Tests ruckeln – das ist normal.
Bleibt die Anzeige länger stehen oder setzt der Treiber zurück (amdgpu/i915-Meldungen
in den Kernel-Logs), gilt der Test als nicht bestanden, auch ohne Fehlerzähler.
<b>Sinnvoll</b> für jede AMD-/Intel-GPU und als Treiber-unabhängiger Gegencheck bei
NVIDIA; für 10 Minuten als Schnelltest, 1 Stunde für Stabilität. <b>Nicht sinnvoll</b>
für Server-Grafikchips (Matrox/ASPEED) – dort gibt es nichts zu belasten.
</p>

<hr>

<h2>Tab: Gesamttest</h2>
<p>
Der Gesamttest führt die markierten, nicht-destruktiven Prüfungen nacheinander aus
und erstellt ein Klartext- und ein eigenständiges HTML-Protokoll. <b>Kein Teiltest ist
vorausgewählt</b> – nur „Begleitendes Monitoring mit Sicherheitsabbruch“ ist aktiv.
Vor dem Start also bewusst zusammenstellen, was geprüft werden soll.
</p>
<p><b>CPU und Arbeitsspeicher:</b> CPU-Stresstest (stress-ng) mit Dauer und
<b>CPU-Lastart</b>: „Volllast (maximale Hitze)“ belastet mit allen cpu-Methoden und
Matrixrechnung, „Mischlast mit Ergebnisprüfung“ verteilt die Worker auf viele
Rechenwerke (Ganzzahl, Matrix, FMA, Gleitkomma, Vektor, Trigonometrie, L1-Cache,
zlib). Beide prüfen die Rechenergebnisse (<code>--verify</code>); jedes falsche
Ergebnis lässt den Schritt fehlschlagen. „OpenDCDiag – stille Datenfehler je Kern“
(nur wenn installiert, Standard 5 min) und „RAM-Verifikation (stress-ng)“ mit
„RAM-Anteil des verfügbaren Speichers“ (Standard 70 %; bei knappem RAM oder
laufenden Diensten niedriger wählen).</p>
<p><b>Datenträger und GPU:</b> „NVMe-Inventar und SMART-Gesundheit“, „Vollständiger
NVMe-Lesetest“, „Vollständiger SATA/SAS/USB-Lesetest“ (liest gewählte
<code>/dev/sd*</code> komplett, nur lesend, SMART vorher/nachher) und – falls
<code>zpool</code> vorhanden – „ZFS-Scrub aller importierten Pools“. Zuerst
„Laufwerke aktualisieren“ und die Laufwerke per Häkchen wählen („Alle auswählen“);
ohne Auswahl prüft SMART alle NVMe, Lesetests brauchen eine Auswahl. Ein kompletter
Lesetest kann Stunden dauern. „NVIDIA-Treiber und GPU-Inventar“, „NVIDIA-DCGM-Diagnose“
mit Stufe 1–4 (nur mit installiertem DCGM) und „GPU-Lasttest (Vulkan) auf allen GPUs“
mit einstellbarer Dauer (80 % des freien Grafikspeichers, bei integrierten GPUs
höchstens 25 % des gemeinsamen Speichers; ohne Vulkan-Loader deaktiviert, ohne
Vulkan-GPU übersprungen).</p>
<p><b>Netzwerk:</b> Zielhost, Ping-Test (Paketanzahl) und iperf3-Durchsatztest (Dauer;
auf dem Ziel muss <code>iperf3 -s</code> laufen). <b>Grenzwerte</b> für maximalen
Paketverlust, mittlere Latenz, Jitter und minimalen Durchsatz gelten für jeden
Durchlauf. Mindestdurchsatz 0 = ausgeschaltet, andere Null-Grenzwerte sind strikt.
Fehlende oder ungültige Messwerte sind kein bestandener Test; ein Grenzwertfehler
kann auch bei Exit-Code 0 auftreten und bleibt im Gesamtbericht erhalten.</p>
<p><b>Ablauf und Protokoll:</b> Protokollordner (Standard „Downloads“), „Bei erstem Fehler abbrechen“, <b>Durchläufe</b> 1–10.000 des gesamten
Plans und optional eine <b>Endzeit</b>: Danach startet kein Teiltest mehr, ein
laufender wird zu Ende geführt – praktisch für Nachtläufe bis zum Arbeitsbeginn.
<b>Begleitendes Monitoring mit Sicherheitsabbruch</b> (Standard an) zeichnet
Temperaturen, Lüfter, ECC und Kernel-Fehler über alle Durchläufe auf und bricht bei
Überschreiten der Temperaturgrenze (Standard 100 °C, Messintervall 10 s) oder neuen
unkorrigierbaren ECC-Fehlern ab; „Individuelle Temperaturgrenzen …“ setzt
Grenzen je Sensor (geteilt mit dem Tab Langzeitmonitoring). Die 100 °C sind ein
Startwert – für SSDs und viele Chipsätze deutlich niedriger ansetzen.</p>
<p>
Benötigt der Plan Root-Rechte (NVMe, Lesetests, OpenDCDiag, DCGM, ZFS), wird das
Passwort <b>nur einmal direkt beim Start</b> abgefragt; danach läuft der Plan
unbeaufsichtigt. Wird die Abfrage abgebrochen, startet der Plan nicht. Ein
manueller Abbruch („Gesamttest abbrechen“) stoppt den aktuell laufenden Prozess und
speichert die bis dahin vorliegenden Ergebnisse. Der Plan läuft nur, solange die App
geöffnet bleibt. Keine Dateisystemreparatur und kein ZFS-Import im Plan.
</p>
<p>
Nach jedem Lauf entstehen Klartext-, HTML- und CSV-Protokoll mit Zeitstempeln,
Zusammenfassungen und Detailausgaben („HTML-Protokoll öffnen“; im Browser über die
Druckfunktion als PDF speicherbar). Die CSV enthält eine Zeile je Teiltest/Durchlauf;
Text und HTML bleiben kompakt mit Ergebniszählern, letztem Ergebnis und erstem Fehler.
„Durchläufe vollständig“ zählt auch vollständig ausgeführte fehlerhafte Durchläufe.
Ping und iperf3 werden nur als Summenwerte protokolliert. Am Anfang beider Berichte
steht der Abschnitt <b>System</b> (CPU, Mainboard/BIOS, Speichermodule wie im Tab
„System“); auf ProLiant-Servern kommt der Schritt <i>ProLiant / RAID-Controller</i>
hinzu (siehe „ProLiant Info“).
</p>
<p><b>Sinnvoll:</b> Abnahme neuer oder reparierter Systeme mit Bericht, Nacht- und
Wochenendläufe über mehrere Durchläufe, Reproduktion sporadischer Fehler.
<b>Nicht sinnvoll:</b> gezielte Fehlersuche an einer einzelnen Komponente – dafür
die Einzeltabs mit mehr Optionen verwenden. Der Lesetest erkennt Lesefehler, prüft
aber keine Dateiinhalte oder Dateisystem-Prüfsummen. Ausreichend freien Speicher
und Kühlung vorsehen.</p>

<hr>

<h2>Tab: System</h2>
<p>
Technische Übersicht ohne Last: Prozessor je Sockel (Modell, Family/Model/Stepping,
Microcode, Kerne – bei Hybrid-CPUs getrennt nach P- und E-Kernen –, Threads,
Taktbereich, wichtige Befehlssätze, vom Kernel gemeldete CPU-Fehler, alle Flags),
Cache-Größen, aktueller Takt je logischer CPU (alle 2 s, nur bei sichtbarem Tab),
Mainboard/BIOS und die <b>Speichermodule</b> je Steckplatz (Größe, Typ, Bauform,
Nenn- und Ist-Takt, Rank, ECC, Spannung, Hersteller, Teile- und Seriennummer).
</p>
<p>
Die Speichermodule liest <code>dmidecode</code> mit Root-Rechten. Das .deb erlaubt
Mitgliedern der Gruppe sudo genau <code>dmidecode -t 1 -t 4 -t 16 -t 17</code> ohne Passwort;
sonst erscheint „Speichermodule und Netzteile mit Passwort auslesen“ (liest alle DMI-Tabellen inkl. Netzteile). „In Zwischenablage kopieren“
liefert alles als Text, z. B. für Tickets.
</p>
<p>
Darunter folgen weitere Komponenten (ohne root, aus <code>/sys</code>, <code>lspci</code>,
<code>ethtool</code>, <code>lsblk</code> und – falls vorhanden – <code>nvidia-smi</code>):
<b>PCIe-Verbindungen</b> der Endgeräte mit aktueller und möglicher Generation/Breite
(möglich = Minimum aus Karte und Steckplatz). <span style="color:#c62828">Rot</span>: weniger
Lanes als möglich – Sitz der Karte, Riser oder Steckplatz prüfen.
<span style="color:#ef6c00">Orange</span>: niedrigere Generation – im Leerlauf durch
Energiesparen (ASPM) oft normal, unter Last erneut prüfen. Außerdem <b>Netzwerk</b> (Modell,
Treiber, Firmware, MAC, Zustand, Geschwindigkeit), <b>Grafik</b> (Treiber, VRAM, VBIOS),
<b>Speicher-Controller</b> (SATA/SAS/RAID/NVMe mit Firmware) und <b>Datenträger</b> (Modell,
Größe, HDD/SSD, Anschluss, Seriennummer, Firmware, Sektorgröße). Diese Angaben stehen auch
im System-Abschnitt des Gesamttest-Berichts.
</p>
<p>
<b>Fibre-Channel-HBAs</b> (QLogic/Marvell qla2xxx, Emulex/Broadcom lpfc) werden aus
<code>/sys/class/fc_host</code> gelesen: Port-Zustand, Geschwindigkeit, WWPN/WWNN,
Fabric, Firmware, Treiber und die Anzahl erreichbarer Remote-Ports. Die Fehlerzähler
(Link-Failures, Loss of Sync/Signal, CRC, Error Frames) werden angezeigt;
<span style="color:#c62828">rot</span> bei Zählern &gt; 0 oder „Linkdown“ mit gestecktem
Kabel. Ohne Fabric lässt sich ein Port mit einem Loopback-Stecker prüfen: Zustand
„Online“, Zähler bleiben ruhig.
</p>
<p>
<b>Netzteile / Redundanz:</b> Die DMI-Tabelle (Typ 39, braucht Root wie die
Speichermodule) zeigt Einbauort, Hersteller, Modell, Seriennummer, Maximalleistung,
Status und ob ein Netzteil bestückt und hot-plug-fähig ist. Auf Servern mit IPMI
(<code>ipmitool</code>) kommen die Netzteil-Sensoren und der Redundanzstatus hinzu
(„Fully Redundant“, „Redundancy Lost“, „AC lost“). Redundanz-Test: bei zwei bestückten,
fehlerfreien Netzteilen ein Netzkabel ziehen – der Server muss weiterlaufen und IPMI
„Redundancy Lost“ melden; nach dem Einstecken muss „Fully Redundant“ zurückkehren.
Ein künstlicher Lasttest des Netzteils findet nicht statt.
</p>

<hr>

<h2>Tab: Informationen</h2>
<p>
Kurzüberblick („Systemstatus“, <code>fastfetch</code>) und Schaltflächen für
Rohausgaben: <code>lshw -class cpu/memory/storage/disk/raid/scsi/display/network</code>,
<code>lspci -vvv</code> und <code>dmesg</code> (mit sudo, falls nicht root).
„Systemdaten aktualisieren“ liest neu ein, „Systemreport (JSON)“ speichert alles
gesammelt für Tickets oder den Vergleich vor/nach einem Umbau. Für eine aufbereitete
Darstellung den Tab „System“ verwenden; dieser Tab ist für die Rohdaten gedacht.
</p>

<hr>

<h2>Tipps für gründliches Testen</h2>
<ol>
  <li>Zuerst <b>Swap deaktivieren</b> und <b>Langzeitmonitoring</b> mit passenden
      Temperaturgrenzen starten (oder im Gesamttest das Begleitmonitoring nutzen).</li>
  <li><b>Tab „System“</b> und <b>SMART/NVMe-Gesundheit</b> vorher festhalten
      (Takte, PCIe-Breite, Reallocated/Pending Sectors, Percentage Used).</li>
  <li><b>OpenDCDiag</b> 10 Minuten als Schnelltest auf stille Rechenfehler.</li>
  <li><b>stress-ng (RAM-Test)</b> oder <b>RAM-Fuelltest</b> für 2+ Stunden laufen lassen.</li>
  <li><b>Prime95 (Blend)</b> für 4+ Stunden – der Goldstandard.</li>
  <li><b>Speichercontroller-Test</b> mit allen Stressoren für 2+ Stunden
      (besonders bei Dual-Socket mit <code>numa</code>).</li>
  <li><b>Disk (Geräte)</b> bzw. <b>NVMe-Lesetest</b> – alle Laufwerke einmal komplett lesen
      (nach RAID-Rebuild besonders wichtig).</li>
  <li><b>Disk (Destruktiv)</b> – bei neuen Laufwerken mindestens 1 Durchlauf
      mit Schreib-/Lese-Verifikation.</li>
  <li><b>GPU-Lasttest</b> (Vulkan bzw. NVIDIA) 30+ Minuten, bei Rechenzentrums-GPUs
      DCGM-Stufe 3.</li>
  <li><b>SMART-Info</b> nach dem Test erneut prüfen und mit vorher vergleichen;
      <b>HPE RAID-Info</b> auf Controller-/Drive-Warnungen prüfen.</li>
  <li><b>Kernel-Logs</b>, <b>MCE-Logs</b> und bei ECC-RAM die <b>EDAC-Anzeige</b>
      auf Fehler prüfen.</li>
  <li>Für die Abnahme mit Bericht: <b>Gesamttest</b> mit Endzeit über Nacht.
      Optional: <b>memtest86+</b> über Nacht laufen lassen (außerhalb des OS).</li>
</ol>

<hr>

<h2>Update-Prüfung</h2>
<p>
Beim Start fragt Hardwaretest im Hintergrund das neueste Release auf GitHub ab.
Ist eine neuere Version verfügbar, erscheint ein Hinweis mit Download-Link
(AppImage bzw. .deb passend zur Installation). „Diese Version nicht mehr anzeigen“
blendet den Hinweis für genau diese Version aus. Manuell: Menü
<b>Hilfe / Help → Nach Updates suchen</b>. Ohne Internet bleibt die Prüfung stumm.
Abschalten mit der Umgebungsvariable <code>HARDWARETEST_NO_UPDATE_CHECK=1</code>.
</p>

<h2>Lizenz</h2>
<p>
Diese Software steht unter der <b>MIT-Lizenz</b>.
Sie darf frei verwendet, verändert und weitergegeben werden, sofern der
Copyright-Hinweis und der Lizenztext erhalten bleiben. Die Software wird
„wie besehen" ohne jegliche Gewährleistung bereitgestellt.
</p>
<p style="color:#888;">© 2026 Norbert Jander · Hardwaretest v__VERSION__</p>
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
<p style="color:#888;">Version __VERSION__</p>

<h2>Overview</h2>
<p>
This application checks the stability of CPU, RAM, memory controllers, storage
devices (SATA/SAS/NVMe), graphics cards and network, reads filesystems, ZFS pools
and HPE ProLiant data, and monitors the system over long periods. All tests run on
Linux using proven tools such as <b>stress-ng</b>, <b>Prime95 (mprime)</b>,
<b>OpenDCDiag</b>, <b>fio</b>, <b>nvme-cli</b>, <b>smartctl</b>, <b>ipmitool</b>
and <b>iperf3</b>.
</p>
<p>
<b>Which tab for what?</b> Individual tests (stress-ng, Prime95, OpenDCDiag, Memory
Controller, RAM fill test, Disk, NVMe, GPU, Network) for targeted troubleshooting;
<b>Test plan</b> for an acceptance run with report; <b>Filesystem</b>,
<b>ProLiant Info</b>, <b>System</b> and <b>Information</b> are read-only;
<b>Long-term monitoring</b> accompanies tests or continuous operation. All tests are
non-destructive – except <b>Disk (Destructive)</b>.
</p>

<h2>General Usage Notes</h2>
<ul>
  <li><b>Reports:</b> every tab automatically writes an HTML report after each test to
      <code>~/Downloads/hwtest-&lt;test&gt;-YYYYMMDD-HHMMSS.html</code> (e.g.
      <code>hwtest-stress-ng-…</code>, <code>hwtest-gesamttest-…</code>). All reports share
      the same layout: status, summary, parameters, sections, hints and raw output.
      "Open report" shows the most recent report; the full test additionally writes a
      CSV with the same name.</li>
  <li><b>Duration:</b> Hours / Minutes / Seconds – total runtime of the test.</li>
  <li><b>CPU workers (threads):</b> Number of test workers on logical CPUs,
      not physical cores. Example: 32 physical cores with hyper-threading
      provide 64 logical CPUs. The GUI reserve is a minimum in logical CPUs;
      whole physical cores including HT siblings remain free. With 64 logical
      CPUs and a reserve of 1, normally 62 test threads are available.
      The actual reserve is shown at the top.</li>
  <li><b>Disable Swap:</b> Strongly recommended for RAM tests, so that physical
      memory is tested rather than swapping to disk.</li>
  <li><b>Temperature Monitor:</b> Shows a per-socket summary (Min/Avg/Max). On
      dual-socket systems with many cores the display automatically becomes
      compact. The detail view shows as many cores per page as fit (at most 24);
      use “Previous/Next” to browse additional cores. Identical core names on
      several sockets get a prefix (S0, S1).<br><i>Core numbers:</i> They come
      from the processor itself (hardware core ID, driver <code>coretemp</code>),
      not from the app, so they often have gaps and are unsorted. Example Intel
      hybrid CPU with 6 P- and 4 E-cores: P-cores are Core 0, 4, 8, 12, 16, 20,
      E-cores Core 24–27. Hyper-threads share one core and sensor, so there are
      fewer entries than logical CPUs.</li>
  <li><b>ECC/EDAC:</b> Displays correctable and uncorrectable memory errors
      (only with ECC RAM and the EDAC kernel module loaded).</li>
  <li><b>Pass/Fail:</b> After the test, the result is automatically evaluated
      based on the output and exit code.</li>
  <li><b>Hardware errors (rasdaemon):</b> If <code>rasdaemon</code> is installed
      (always on the live ISO), every test also evaluates the hardware errors
      logged during the test: CPU machine checks (cache, bus, memory controller),
      EDAC memory errors, PCIe AER and memory pages retired by the kernel. An
      <b>uncorrected</b> error (UE) fails the test. <b>Corrected</b> errors (CE)
      are only a warning in the log and in the test plan report – they happen
      occasionally, but many of them point to a weak module or core. Note: the
      kernel may report corrected CPU errors up to 5 minutes late, so very short
      tests can miss them. All entries: <code>sudo ras-mc-ctl --errors</code>.</li>
  <li><b>Helper buttons:</b> "Refresh system values" re-reads free RAM, swap and
      CPU count. "Start btop" opens the system monitor, "Kernel logs" and
      "MCE logs" show <code>dmesg</code> and machine-check entries – look at them
      once after every test.</li>
  <li><b>Root privileges:</b> device tests, NVMe, Filesystem, ProLiant Info and parts
      of OpenDCDiag/System ask for the administrator password. On the live ISO the
      prompt is usually skipped; in the .deb, sudoers rules allow individual
      commands without a password.</li>
  <li><b>Concurrency:</b> run only one stress test at a time – parallel tests
      distort temperatures, throughput and error attribution. Only long-term
      monitoring is meant as a companion.</li>
</ul>

<hr>

<h2>Tab: stress-ng</h2>
<p>
Flexible stress test for CPU and RAM with multiple modes:
</p>
<table>
  <tr><th>Mode</th><th>Description</th><th>Use Case</th></tr>
  <tr>
    <td><b>CPU load (maximum heat)</b></td>
    <td>Maximum CPU utilization (matrix, all methods)</td>
    <td>Test CPU stability and cooling</td>
  </tr>
  <tr>
    <td><b>CPU mixed load with result verification</b></td>
    <td>Integer, floating point, FMA, vector, matrix, L1 cache and compression
        at the same time, each stressor with <code>--verify</code></td>
    <td>Find compute errors of individual units</td>
  </tr>
  <tr>
    <td><b>RAM test (find errors)</b></td>
    <td>Aggressive memory test with <code>--verify</code></td>
    <td>Find RAM errors</td>
  </tr>
  <tr>
    <td><b>RAM bandwidth (throughput)</b></td>
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
<b>RAM (MB):</b> Total memory for the test; it is split evenly across the workers.
For RAM tests use as much free memory as possible, but leave a reserve for OS and
GUI (without swap the system would otherwise freeze).<br>
<b>CPU workers / GUI reserve:</b> see "General Usage Notes". The reserve keeps the
UI responsive; use 1 for maximum heat, 2 for long runs.<br>
<b>Disable / enable swap:</b> turns swap off or on system-wide (root).
"Check memtest86+" shows whether memtest86+ is available in the boot menu.<br>
<b>Useful:</b> quick first check (10–30 min) and cooling test; RAM test as a
complement to the RAM fill test. <b>Not suitable</b> as the sole RAM proof – use the
RAM fill test and memtest86+ (outside the operating system) for that.
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
<b>Worker threads:</b> default = logical CPUs minus GUI reserve.<br>
<b>Direct mode</b> ("Only mprime -t"): uses the existing <code>prime.txt</code>/<code>local.txt</code>
without overwriting them – only for your own hand-maintained configurations.<br>
<b>What to watch:</b> Prime95 reports errors only after complete passes; below
1 hour the result is weak, 4+ hours of Blend are considered reliable. Watch the
cooling – Small FFTs push the CPU to its temperature limit.
<b>Useful</b> for CPU, power delivery and memory timings;
<b>not useful</b> for storage or GPU.
</p>

<hr>

<h2>Tab: OpenDCDiag</h2>
<p>
<b>OpenDCDiag</b> (Intel, Apache 2.0) looks for <b>silent data errors</b>: cores that compute
wrong results under load without crashing or reporting a machine check. Every core runs
workloads with known results (Eigen matrices, zlib/zstd compression, Intel In-Field Scan)
and compares them. Works on Intel and AMD.
</p>
<ul>
  <li><b>Result:</b> Faulty cores are reported with socket, core and logical CPU,
      e.g. <code>S1 Core 3 (CPU 35)</code>. A single error means: replace the processor.</li>
  <li><b>CPU selection:</b> "All CPUs" (default), "Socket N only" per detected socket
      or "Custom …" – this enables the text field for OpenDCDiag's
      <code>--cpuset</code> syntax: logical CPUs/threads, e.g. <code>0-7,16</code>;
      <code>p1</code> = socket 1; <code>c3</code> = core 3. Useful to re-test a
      suspicious socket or core specifically.</li>
  <li><b>Build:</b> On CPUs without AVX2 (e.g. ProLiant Gen8) the generic build is chosen
      automatically.</li>
  <li><b>Permissions:</b> Without root, tests needing MSR access and In-Field Scan are skipped
      (listed in the log). In the test plan OpenDCDiag runs in the root session. The .deb lets members of the
      sudo group start OpenDCDiag as root without a password
      (/etc/sudoers.d/hardwaretest-opendcdiag); the tab uses this automatically.</li>
  <li><b>Duration:</b> 10 minutes as a quick test, 1 hour or more for acceptance testing.</li>
  <li><b>What to watch:</b> OpenDCDiag complements Prime95/stress-ng but does not
      replace them – it finds compute errors per core, not cooling or RAM problems.
      Is an error reproducible? Narrow it down with "Socket N only" and replace the
      processor.</li>
</ul>

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
  <li><b>What to watch:</b> The test produces little heat but high bus load; errors
      usually show up as a machine check or EDAC entry, not as a crash – so check
      "MCE logs" and the EDAC display after the run. <b>Useful</b> on multi-socket
      systems, after RAM/CPU replacement and for sporadic crashes with no findings in
      Prime95; <b>of little use</b> on single-socket systems as a first check – there
      stress-ng and the RAM fill test are sufficient.</li>
</ul>

<hr>

<h2>Tab: RAM fill test</h2>
<p>Each cycle allocates almost all free RAM, writes one bit pattern (in parallel
on all cores except two), verifies it, frees the memory and repeats. Blocks move
to different CPUs every cycle.</p>

<h3>Cycle steps</h3>
<ol>
<li><b>Allocate:</b> available RAM minus the reserve, in 256 MB blocks.</li>
<li><b>Write:</b> one pattern per cycle, each process pinned to one CPU. Two cores
stay free for the OS/GUI; they change every cycle so that all cores are used over
several cycles.</li>
<li><b>Lock (mlock):</b> written memory is locked against swapping so that RAM,
not swap, is tested.</li>
<li><b>Verify:</b> by <i>different</i> CPUs than the writers (on multi-socket
systems this also goes through the other memory controller).</li>
<li><b>Moving inversion</b> (if enough time is left): read and
compare again, write the inverted pattern, verify it. Finds bits that flip over
time and coupling faults when all bits toggle.</li>
<li><b>Free</b> and short pause (the memory drop is visible in btop). The test memory
is shmem (shown as "Cached" in btop) and is punched out at once via MADV_REMOVE. With very
large RAM (terabytes) the kernel needs some time until the memory is reported as
available again; the test waits for it (about 1 s per 8 GB, max. 10 min) and logs
the waiting time.</li>
</ol>

<h3>Patterns</h3>
<ul>
<li>0xAA, 0x55, 0xFF, 0x00 – stuck bits.</li>
<li>Walking one/zero per byte – bit aliasing.</li>
<li>Random 64-bit words and random data (change every round).</li>
<li>Address-in-data and inverted – address decoder faults.</li>
<li><b>Data line test</b> (once per pattern round): 64-bit walking
one/zero over 128 MB. Reports the affected data bits.</li>
</ul>

<h3>Options</h3>
<ul>
<li><b>Memory:</b> "automatic" (0) = all available RAM minus the reserve; a fixed
value allocates exactly this amount per cycle.</li>
<li><b>Reserve (OS/GUI):</b> kept free with automatic memory size
(default 512 MB; the test enforces at least 1 % of RAM).</li>
<li><b>Block size:</b> size of the individual blocks (default 256 MB). Smaller blocks
help with fragmented memory, larger ones are faster.</li>
<li><b>Pause per cycle:</b> waiting time with memory released (default 3 s) so the
drop is visible e.g. in btop. 0 = continue immediately.</li>
<li><b>Disable swap</b> before starting, otherwise test memory gets paged out and
the result is diluted.</li>
</ul>

<h3>System protection</h3>
<ul>
<li><b>Reserve (OS/GUI):</b> kept free; at least 1 % of RAM (1–8 GB) is enforced.</li>
<li><b>Memory pressure:</b> if free RAM drops below half the reserve during the
test (e.g. another program grows), blocks are discarded from the end and not
verified. This is logged.</li>
<li><b>OOM protection:</b> if memory still runs out, the kernel kills the test
first, not the OS or GUI.</li>
<li class="bad"><b>Setting memory manually:</b> always leave enough free memory for
the operating system (recommended: at least 2–4 GB or 1–2 % of RAM). Without swap
the test memory cannot be paged out – if it is too large the system freezes.</li>
</ul>

<h3>Error messages</h3>
<p><code>MISMATCH bei Offset …</code> shows the position in the test memory and the
expected and read value. When run as root the <b>physical address</b> is added,
which helps to locate the DIMM via vendor documentation or
<code>edac-util</code>. Without root it is omitted.</p>

<h3>Limits</h3>
<ul>
<li>Memory used by the kernel, drivers and running programs as well as the
reserve is <b>never</b> tested.</li>
<li>The test runs inside an operating system using virtual addresses; the kernel
decides which physical pages are used. Coverage is not complete.</li>
<li>Without root, locking is limited by <code>ulimit -l</code>; with active swap
part of the memory may be paged out. A warning is shown.</li>
<li>ECC memory corrects single-bit errors itself; the test does not see them –
check the system log, EDAC or iLO. The test finds uncorrectable or unprotected
errors.</li>
<li>Python reaches about 2–4 GB/s per core. With very large RAM a cycle may not
fit into the test time; then only the written part is verified.</li>
<li>For a complete check (including hammer and bit-fade tests) use MemTest86
from the boot menu.</li>
</ul>

<h2>Tab: Disk (File / Device / Destructive)</h2>
<p>
Disk/SSD tests using <b>fio</b> – optimized for HPE ProLiant servers
with SmartArray RAID controllers, SAS/NVMe drives.
</p>

<h3>File Test (safe)</h3>
<ul>
  <li>Reads/writes to a temporary file on any filesystem
      (set the "test file" via "Choose path").</li>
  <li>No root access needed – safe on production systems.</li>
  <li>Workload selectable: <code>read</code>, <code>write</code>, <code>randread</code>,
      <code>randwrite</code>, <code>readwrite</code>, <code>randrw</code>.</li>
  <li><b>Block size</b> (default 128k; 4k for IOPS, 1M for throughput), <b>file size</b>
      (default 4096 MB – choose it well above RAM and drive cache, otherwise only the
      cache is measured), <b>iodepth</b> (32) and <b>jobs</b> (parallel fio processes).</li>
  <li>Write workloads automatically enable <b>CRC32c verification</b>
      (<code>--verify=crc32c --do_verify=1 --verify_fatal=1</code>).</li>
  <li><span class="warn">Live ISO:</span> If the target folder lives in RAM (overlay,
      tmpfs, ramfs, squashfs), the test refuses to start – it would only measure RAM.
      Choose a mounted drive under <code>/media</code> or <code>/mnt</code>.</li>
  <li>Suitable for: filesystem integrity tests, SSD endurance, IOPS benchmarks on drives
      holding data. <b>Not suitable</b> for finding bad sectors – use the device test,
      which reads every block.</li>
</ul>

<h3>Device Test (read-only)</h3>
<ul>
  <li>"Start raw-device read" reads all blocks directly from the block device via
      <code>fio</code> as root (sudo password dialog).</li>
  <li>Detects bad sectors, SCSI errors and medium errors – especially important
      after RAID rebuilds on HPE SmartArray.</li>
  <li>Uses <code>continue_on_error=read</code>: continues reading all blocks even
      if some fail → complete error picture.</li>
  <li>No data modification – safe on existing systems. Mounted drives remain
      selectable. There is no time limit: the run ends when all blocks have been read
      (several TB = hours); progress and throughput are displayed.</li>
</ul>

<h3>Destructive Test <span class="bad">(⚠ DATA LOSS!)</span></h3>
<ul>
  <li>"Start DESTRUCTIVE test" overwrites <b>all data</b> on the selected devices and
      verifies by re-reading with CRC32c.</li>
  <li><code>verify_backlog=16384</code> / <code>verify_backlog_batch=4096</code> –
      optimized for large RAID volumes (multi-TB).</li>
  <li>"Write/read passes" 1–10 for intensive endurance testing.</li>
  <li><span class="bad">Only use for empty/new drives!</span> Partition table and
      filesystems are gone afterwards. Useful for new hardware, before commissioning
      RAID members and as proof for warranty claims; on SSDs every pass consumes write
      endurance (TBW).</li>
</ul>

<h3>Drive Selection &amp; Unmounting</h3>
<ul>
  <li>Select drives individually via their checkbox; "Select all" / "Clear selection"
      only affect selectable entries.</li>
  <li>In the "Destructive" tab <b>no</b> drive is selected initially.</li>
  <li>Mounted or busy drives (mounts, swap, LVM/LUKS, mdraid, ZFS) are marked "belegt"
      there and <b>locked</b>. In the "Device" (read-only) tab they remain selectable.</li>
  <li><b>Unmount drive…</b>: unmounts all partitions of the chosen drive via
      <code>udisksctl</code> (fallback <code>umount</code> as root) and rescans.
      The system drive (<code>/</code>, <code>/boot</code>, <code>/home</code>, swap …)
      is not offered.</li>
  <li>Right before starting, the drives are checked again to be free and
      unchanged (serial/WWN).</li>
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
  <li><b>RAID controller status</b>: Jumps to the structured evaluation of
      HPE Smart Array / MegaRAID controllers in the “ProLiant Info” → “RAID” tab.</li>
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

<h2>Tab: NVMe</h2>
<p>
Diagnostics and non-destructive tests for NVMe drives with <code>nvme-cli</code>
(root privileges, password asked once) and <code>fio</code>.
</p>
<ul>
  <li><b>Scan NVMe drives</b> lists all namespaces; the drive for diagnostics/self-test
      is chosen in the drop-down list.</li>
  <li><b>Read SMART / health data:</b> critical warnings, temperature, percentage used,
      media and error-log entries, data read/written, power cycles and unsafe shutdowns.
      <span class="warn">Watch for:</span> <i>critical warning</i> ≠ 0,
      <i>percentage used</i> ≥ 90 %, rising <i>media errors</i> or
      <i>unsafe shutdowns</i>.</li>
  <li><b>Self-test:</b> "Short NVMe self-test" (a few minutes) or "Extended NVMe
      self-test" (up to hours depending on the drive) run inside the drive firmware;
      progress is displayed. "Read self-test log" shows the latest results. No read
      test is possible while a self-test is running.</li>
  <li><b>NVMe read tests</b> (read-only, one or more target drives): the
      <b>read benchmark</b> measures sequential or random throughput over the set
      duration (default 30 s); the <b>full read test</b> reads every block of all
      selected namespaces in parallel – finds unreadable blocks but can take many
      hours. "Stop read test" aborts.</li>
  <li><b>Useful:</b> compare SMART before and after every stress test; extended
      self-test and full read test when read errors are suspected or before
      commissioning. <b>Not useful:</b> benchmarks on a drive the system is currently
      using – the values are not comparable then. High temperatures (> 70 °C) during the
      read test indicate missing cooling of the M.2 slot.</li>
</ul>

<hr>

<h2>Tab: Network</h2>
<p>
This tab lists all detected network adapters with link state, driver, PCI address,
link speed, duplex, MTU, MAC and IPv4/IPv6 addresses. With “Automatic (routing)”,
Linux chooses the route. Selecting an adapter binds ping and iperf3 to that interface.
</p>
<ul>
  <li><b>Ping</b> measures packet loss, minimum/average/maximum latency and jitter.</li>
  <li><b>iperf3</b> measures TCP throughput and retransmits; the target must run
      <code>iperf3 -s</code>.</li>
  <li>Thresholds determine pass/fail independently of the process exit code
      (defaults: packet loss 0 %, average latency 100 ms, jitter 20 ms, minimum
      throughput 0 = off). Zero thresholds other than throughput are strict.</li>
  <li><b>What to watch:</b> The target must be reachable in the same network and allow
      ping; without a running <code>iperf3 -s</code> the throughput test fails – that is
      not a hardware fault. For meaningful throughput choose a target with the same or
      higher link speed. "Refresh network adapters" after re-plugging cables.
      <b>Useful</b> for link stability, cables/transceivers and driver problems;
      <b>not useful</b> as a measurement over the Internet or Wi-Fi.</li>
  <li><b>ethtool self-test</b> (<code>ethtool -t</code>, root, physical adapters only):
      the driver checks registers, EEPROM, interrupts, loopback and link. "Offline
      (thorough, briefly drops the link)" tests more but interrupts running connections.
      Not every driver supports the test ("Operation not supported" is not a hardware
      fault). Error counters from <code>ethtool -S</code> (CRC, rx/tx errors, dropped)
      are compared before and after the test; an increase is shown as a warning. If the
      driver reports FAIL although every individual test is 0, the test could not run
      (amber, "inconclusive"): <code>bnxt_en</code> (Broadcom NetXtreme-E, e.g. HPE 25G)
      refuses the offline test on NPAR/shared PFs or with active VFs, as well as on an
      interface that is not up – the reason is in <code>dmesg</code>. Use the online test then.</li>
  <li><b>Read SFP module</b> (<code>ethtool -m</code>): vendor, type, serial number,
      temperature, voltage and Rx/Tx power of the transceiver. RJ45 adapters without a
      module show a note.</li>
  <li><b>Mellanox link diagnostics</b> (<code>mstlink</code> from the free
      <code>mstflint</code> package, root): only shown for Mellanox/NVIDIA adapters
      (mlx4/mlx5) when <code>mstflint</code> is installed. Shows link state, speed, FEC,
      firmware recommendation ("No issue was observed"), physical errors/BER, link-down
      counter and module data with limits. <span style="color:#ef6c00">Orange</span> for
      error counters &gt; 0, values outside module limits or any other recommendation;
      <span style="color:#c62828">red</span> without link. All values go to the log.</li>
</ul>

<hr>

<h2>Tab: NVIDIA GPU</h2>
<p>
An appropriate NVIDIA driver must be installed and <code>nvidia-smi</code> must
work for inventory and health data. Active tests additionally require NVIDIA
DCGM 4 using the package matching the driver, such as
<code>datacenter-gpu-manager-4-cuda12</code> or <code>-cuda13</code>, with the
<code>nvidia-dcgm</code> service running. CUDA Toolkit, PyTorch and Docker are not
required. DCGM level 1 is a quick readiness check; levels 2–4 increasingly test
memory, PCIe, compute load and other hardware functions – level 3 (about 30 min)
or 4 (longer) runs all DCGM tests in sequence (full GPU test).
The inventory also shows current GPU/SM/memory clocks and the current and maximum
PCIe generation and link width. Live monitoring samples utilization, temperature,
VRAM, power and clocks of the selected GPUs at the configured interval. It starts
automatically with a DCGM diagnostic.
The <b>GPU stress test</b> works without DCGM on every NVIDIA GPU from Turing on
(GeForce GTX 16xx/RTX 20xx–50xx, RTX/Quadro, data-center GPUs). It only needs the
NVIDIA driver (libcuda.so.1), creates compute load with result comparison and
continuously checks the configured share of free VRAM with bit patterns.
Compute or memory errors result in “FAILED”. No CUDA toolkit is required.
<br><b>Supported NVIDIA GPUs</b> (driver 595, open kernel module, Turing and newer):
GeForce GTX 16xx and RTX 20xx/30xx/40xx/50xx; RTX/Quadro from Turing on;
data center: Tesla T4, Ampere A2/A10/A16/A30/A40/A100, Ada L4/L40/L40S,
RTX 2000–6000 Ada, Hopper H100/H200, Blackwell B200/RTX PRO.
DCGM diagnostics target data-center GPUs; on workstation cards only parts run,
GeForce cards are not supported.
If <code>dcgmi</code> is not installed, the “DCGM diagnostics” section is hidden
(as is the DCGM option in the test plan).
After every DCGM diagnostic an <b>HTML report</b> is written to <code>~/Downloads</code>
(<code>dcgm-diagnose-DATE.html</code>): overall verdict, data sheet of all tested GPUs
(model, serial number, UUID, VBIOS, PCIe generation/width, ECC, retired/remapped pages,
power limits, clocks, temperature) and the result table per test and GPU.
“Open HTML report” shows it in the browser. “GPU data sheet (HTML)” creates the same
data sheet without running a diagnostic (<code>gpu-datenblatt-DATE.html</code>).
If, with two or more GPUs, only the <b>pcie</b> test fails with “P2P write verification
failed: buffer mismatch” (ID 86/87), this is not a GPU defect: the peer-to-peer part copies
directly between two GPUs and fails with IOMMU/VT-d plus PCIe ACS enabled or GPUs on
different CPU sockets. Remedy: tick “Skip PCIe peer-to-peer test” (off by default)
(<code>pcie.test_p2p_on=false</code>). If P2P really has to work, there are three knobs:
(1) kernel parameter <code>iommu=pt</code> (or, if needed, <code>intel_iommu=off</code>/<code>amd_iommu=off</code>) in
<code>GRUB_CMDLINE_LINUX_DEFAULT</code>, then <code>update-grub</code> and reboot;
(2) in the BIOS/RBSU under “Advanced Options” disable PCIe Access Control Services (ACS)
or VT-d/IOMMU, provided SR-IOV is not required; (3) driver override
<code>options nvidia NVreg_RegistryDwords="ForceP2P=1"</code> in
<code>/etc/modprobe.d/nvidia.conf</code> (replace an existing <code>ForceP2P=0</code>), then reboot.
On Synergy blades the GPUs sit in the graphics expansion module behind PCIe switches and
mezzanine cards – there, skipping the test usually remains the practical choice.
With DCGM 4 the report also shows the overall
status per test (“overall”) when it is worse than the per-GPU results.
Not supported: Pascal/Volta (GTX 10xx, P4, P40, P100, V100) and older.
The complete installation guide is available at
<code>/opt/hardwaretest/docs/NVIDIA_GPU.md</code>.
</p>
<p>
<b>Usage:</b> "Scan NVIDIA GPUs" builds the inventory, "Linux basic check" examines
PCIe devices, bound drivers, kernel modules and device nodes without requiring the
driver (useful when <code>nvidia-smi</code> is missing). Tick the GPUs to test.
<b>GPU stress test:</b> duration (default 10 min) and "Share of free VRAM" (80 %) –
for cards that also drive the desktop, choose 50–60 %.
<b>What to watch:</b> clocks dropping as temperature rises indicate throttling
(cooling/fan curve), VRAM pattern errors indicate defective memory, aborts with Xid
errors in the kernel logs indicate driver/power problems. For acceptance of
data-center GPUs use DCGM level 3 or 4 (hours); level 1 is only a readiness test.
</p>

<hr>

<h2>Tab: GPU (Vulkan)</h2>
<p>
Vendor-neutral stress test for graphics cards from <b>AMD</b> (Radeon, Radeon Pro,
Instinct), <b>Intel</b> (UHD/Iris Xe, Arc) and <b>NVIDIA</b> (via NVK or the
proprietary driver) using Vulkan compute. Only the Vulkan loader
(<code>libvulkan1</code>) and a Vulkan driver such as <code>mesa-vulkan-drivers</code>
(RADV, ANV, NVK) are required. Software renderers (llvmpipe) are hidden.
The test fills the configured share of free graphics memory with changing bit
patterns, verifies them on the GPU (errors are counted) and generates compute load
with result comparison across all threads in between. Compute or memory errors
result in “FAILED”. <b>Integrated GPUs</b> use at most 25 % of the shared memory
so the system stays usable.
Live monitoring reads sysfs without additional tools: temperature, power, load,
GPU/memory clock, VRAM and fan (depending on the driver: amdgpu provides almost
everything, i915/xe mainly the clock; for integrated Intel GPUs also the CPU
package temperature). For integrated Intel GPUs the app derives the load from
<code>/proc/*/fdinfo</code> (like <code>intel_gpu_top</code>/<code>gputop</code>;
without root only for processes of the current user, including the stress test)
and the power from the RAPL “uncore” counter (suffix “(RAPL)”). These counters are
usually readable by root only (“power needs root”); in the HPE Live ISO a udev rule
makes them readable. Clock as current/maximum, 0 MHz when idle. The first line
shows the time of the last update.
Server graphics chips (Matrox, ASPEED) have no Vulkan driver and are only listed
as “without Vulkan”. For NVIDIA cards with the proprietary driver, the “NVIDIA GPU”
tab additionally offers the CUDA stress test and DCGM diagnostics.
</p>
<p>
<b>Usage:</b> "Scan GPUs", tick the desired cards, set the duration (default 10 min)
and "Share of free graphics memory" (80 %), "Start GPU stress test".
<b>What to watch:</b> the desktop may stutter during the test – that is normal. If
the display freezes for longer or the driver resets (amdgpu/i915 messages in the
kernel logs), the test counts as failed even without error counters.
<b>Useful</b> for every AMD/Intel GPU and as a driver-independent cross-check for
NVIDIA; 10 minutes as a quick test, 1 hour for stability. <b>Not useful</b> for
server graphics chips (Matrox/ASPEED) – there is nothing to load there.
</p>

<hr>

<h2>Tab: Test plan</h2>
<p>
The test plan runs the selected non-destructive checks one after another and creates
a plain-text and a self-contained HTML report. <b>No step is preselected</b> – only
"Background monitoring with safety stop" is active. So decide deliberately what to
test before starting.
</p>
<p><b>CPU and memory:</b> CPU stress test (stress-ng) with duration and
<b>CPU load type</b>: "Full load (maximum heat)" uses all cpu methods plus matrix
math, "Mixed load with result verification" spreads the workers over many execution
units (integer, matrix, FMA, floating point, vector, trigonometry, L1 cache, zlib).
Both verify the computed results (<code>--verify</code>); any wrong result fails the
step. "OpenDCDiag – silent data errors per core" (only if installed, default 5 min)
and "RAM verification (stress-ng)" with "RAM share of available memory" (default
70 %; choose less with little RAM or running services).</p>
<p><b>Storage and GPU:</b> "NVMe inventory and SMART health", "Full NVMe read test",
"Full SATA/SAS/USB read test" (reads the selected <code>/dev/sd*</code> completely,
read-only, SMART before/after) and – if <code>zpool</code> exists – "ZFS scrub of all
imported pools". First click "Refresh drives" and tick the drives ("Select all");
without a selection SMART checks all NVMe, the read tests need a selection. A full
read test can take hours. "NVIDIA driver and GPU inventory", "NVIDIA DCGM
diagnostics" with level 1–4 (only with DCGM installed) and "GPU stress test (Vulkan)
on all GPUs" with configurable duration (80 % of free graphics memory, at most 25 % of
shared memory on integrated GPUs; disabled without a Vulkan loader, skipped without a
Vulkan GPU).</p>
<p><b>Network:</b> target host, ping test (packet count) and iperf3 throughput test
(duration; the target must run <code>iperf3 -s</code>). <b>Thresholds</b> for maximum
packet loss, average latency, jitter and minimum throughput apply to every round.
Minimum throughput 0 = off, other zero thresholds are strict. Missing or invalid
measurements are not a passed test; a threshold failure can occur even with exit
code 0 and is kept in the overall report.</p>
<p><b>Flow and report:</b> report folder (default "Downloads"), "Stop on first failure", <b>Rounds</b> 1–10,000 of the whole plan and an
optional <b>End time</b>: no step starts after it, a running step is completed –
handy for overnight runs until the start of work.
<b>Background monitoring with safety stop</b> (default on) records temperatures,
fans, ECC and kernel errors across all rounds and aborts when the temperature limit
(default 100 °C, sampling interval 10 s) is exceeded or new uncorrectable ECC errors
appear; "Individual temperature limits …" sets limits per sensor (shared with the
Long-term monitoring tab). The 100 °C are a starting value – set it much lower for
SSDs and many chipsets.</p>
<p>
If the plan needs root privileges (NVMe, read tests, OpenDCDiag, DCGM, ZFS), the
password is asked <b>only once right at the start</b>; afterwards the plan runs
unattended. If the prompt is cancelled, the plan does not start. A manual abort
("Cancel test plan") stops the currently running process and saves the results
collected so far. The plan only runs while the app stays open. No filesystem repair
and no ZFS import in the plan.
</p>
<p>
Every run produces plain-text, HTML and CSV reports with timestamps, summaries and
detail output ("Open HTML report"; save as PDF via the browser's print function). The
CSV holds one row per step/round; text and HTML stay compact with result counters,
last result and first failure. "Rounds completed" also counts fully executed failed
rounds. Ping and iperf3 are logged as summary values only. Both reports begin with a
<b>System</b> section (CPU, mainboard/BIOS, memory modules as in the "System" tab);
on ProLiant servers the step <i>ProLiant / RAID controller</i> is added (see
"ProLiant Info").
</p>
<p><b>Useful:</b> acceptance of new or repaired systems with a report, overnight and
weekend runs over several rounds, reproducing sporadic errors. <b>Not useful:</b>
targeted troubleshooting of a single component – use the individual tabs with more
options for that. The read test detects read errors but does not verify file
contents or filesystem checksums. Provide enough free memory and cooling.</p>

<hr>

<h2>Tab: System</h2>
<p>
Technical overview without load: processor per socket (model, family/model/stepping,
microcode, cores – split into P and E cores on hybrid CPUs –, threads, clock range,
key instruction sets, CPU bugs reported by the kernel, all flags), cache sizes,
current clock per logical CPU (every 2 s, only while the tab is visible),
mainboard/BIOS and the <b>memory modules</b> per slot (size, type, form factor,
rated and configured speed, rank, ECC, voltage, manufacturer, part and serial number).
</p>
<p>
Memory modules are read by <code>dmidecode</code> with root privileges. The .deb lets
members of the sudo group run exactly <code>dmidecode -t 1 -t 4 -t 16 -t 17</code> without a
password; otherwise "Read memory modules and power supplies with password" appears (reads all DMI tables incl. power supplies). "Copy to clipboard"
returns everything as text, e.g. for tickets.
</p>
<p>
Further components follow below (without root, from <code>/sys</code>, <code>lspci</code>,
<code>ethtool</code>, <code>lsblk</code> and – if present – <code>nvidia-smi</code>):
<b>PCIe links</b> of end devices with current and possible generation/width
(possible = minimum of card and slot). <span style="color:#c62828">Red</span>: fewer lanes
than possible – check card seating, riser or slot. <span style="color:#ef6c00">Orange</span>:
lower generation – often normal at idle due to power saving (ASPM), check again under load.
Also <b>network</b> (model, driver, firmware, MAC, state, speed), <b>graphics</b> (driver,
VRAM, VBIOS), <b>storage controllers</b> (SATA/SAS/RAID/NVMe with firmware) and <b>disks</b>
(model, size, HDD/SSD, transport, serial number, firmware, sector size). This information is
also part of the system section of the full test report.
</p>
<p>
<b>Fibre Channel HBAs</b> (QLogic/Marvell qla2xxx, Emulex/Broadcom lpfc) are read from
<code>/sys/class/fc_host</code>: port state, speed, WWPN/WWNN, fabric, firmware, driver
and the number of reachable remote ports. Error counters (link failures, loss of
sync/signal, CRC, error frames) are shown; <span style="color:#c62828">red</span> for
counters &gt; 0 or "Linkdown" with a cable attached. Without a fabric, a port can be
checked with a loopback plug: state "Online", counters stay quiet.
</p>
<p>
<b>Power supplies / redundancy:</b> the DMI table (type 39, needs root like the memory
modules) shows location, manufacturer, model, serial number, maximum power, status and
whether a supply is populated and hot-pluggable. On servers with IPMI
(<code>ipmitool</code>) the power supply sensors and the redundancy state are added
("Fully Redundant", "Redundancy Lost", "AC lost"). Redundancy test: with two populated,
healthy supplies unplug one power cord – the server must keep running and IPMI must
report "Redundancy Lost"; after plugging it back in "Fully Redundant" must return.
There is no artificial load test of the power supply.
</p>

<hr>

<h2>Tab: Information</h2>
<p>
Brief overview ("system status", <code>fastfetch</code>) and buttons for raw output:
<code>lshw -class cpu/memory/storage/disk/raid/scsi/display/network</code>,
<code>lspci -vvv</code> and <code>dmesg</code> (via sudo if not root).
"Refresh system data" re-reads, "System report (JSON)" saves everything in one file
for tickets or for comparing before/after a hardware change. For a structured view
use the "System" tab; this tab is meant for raw data.
</p>

<hr>

<h2>Tips for Thorough Testing</h2>
<ol>
  <li>First <b>disable swap</b> and start <b>Long-term monitoring</b> with suitable
      temperature limits (or use background monitoring in the test plan).</li>
  <li>Record the <b>System</b> tab and <b>SMART/NVMe health</b> beforehand
      (clocks, PCIe width, reallocated/pending sectors, percentage used).</li>
  <li><b>OpenDCDiag</b> for 10 minutes as a quick test for silent compute errors.</li>
  <li>Run <b>stress-ng (RAM test)</b> or the <b>RAM fill test</b> for 2+ hours.</li>
  <li>Run <b>Prime95 (Blend)</b> for 4+ hours – the gold standard.</li>
  <li>Run the <b>Memory Controller test</b> with all stressors for 2+ hours
      (especially on dual-socket with <code>numa</code>).</li>
  <li><b>Disk (Device)</b> or <b>NVMe read test</b> – read all drives completely once
      (especially important after RAID rebuilds).</li>
  <li><b>Disk (Destructive)</b> – for new drives, at least 1 pass
      with write/read verification.</li>
  <li><b>GPU stress test</b> (Vulkan or NVIDIA) for 30+ minutes, DCGM level 3 for
      data-center GPUs.</li>
  <li>Check <b>SMART Info</b> again after testing and compare with before;
      check <b>HPE RAID Info</b> for controller/drive warnings.</li>
  <li>Check <b>Kernel Logs</b>, <b>MCE Logs</b> and, with ECC RAM, the
      <b>EDAC display</b> for errors.</li>
  <li>For acceptance with a report: <b>Test plan</b> with an end time overnight.
      Optional: run <b>memtest86+</b> overnight (outside the OS).</li>
</ol>

<hr>

<h2>Update check</h2>
<p>
On startup Hardwaretest queries the latest GitHub release in the background.
If a newer version is available, a notice with a download link is shown
(AppImage or .deb depending on the installation). "Don't show this version again"
hides the notice for that version. Manually: menu
<b>Hilfe / Help → Check for updates</b>. Without internet the check stays silent.
Disable it with the environment variable <code>HARDWARETEST_NO_UPDATE_CHECK=1</code>.
</p>

<h2>License</h2>
<p>
This software is released under the <b>MIT License</b>.
You may freely use, modify, and distribute it, provided that the copyright
notice and license text are retained. The software is provided "as is",
without warranty of any kind.
</p>
<p style="color:#888;">© 2026 Norbert Jander · Hardwaretest v__VERSION__</p>
"""


# ---------------------------------------------------------------------------
# Widget
# ---------------------------------------------------------------------------

class HelpPanel(QWidget):
    """Help content controlled by the application's global language choice."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)

        # Scrollbarer Inhalt
        self._content = QLabel()
        # The complete HTML document is switched by ``_switch_language``.
        # It must not be treated as an ordinary, one-line widget caption by
        # the generic UI retranslator in MainWindow.
        self._content._hardwaretest_skip_tree = True
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
        layout.addWidget(scroll)
        self.setLayout(layout)

        language_manager.language_changed.connect(self._switch_language)
        self._switch_language()

    def _switch_language(self, _language: str | None = None) -> None:
        lang = language_manager.language
        if lang == "en":
            self._content.setText(_HELP_EN.replace("__VERSION__", __version__) + """
<h2>Tab: Filesystem (Partitions, ZFS pools)</h2>
<p><b>Filesystem → Partitions:</b> "Refresh drives" lists partitions with filesystem
and mount point. "Status diagnosis (mounted)" is meant for running systems,
"Check structure (offline)" only for unmounted filesystems. Reports go to the
"Log folder …" ("Open HTML log"). The tab is locked while other tests run ("Finish
the other tests first") – checking under load distorts both. <b>Useful</b> after power
failures, before data migrations and after read tests with errors; <b>not useful</b>
as a substitute for a backup.</p>
<p><b>Filesystem → ZFS pools:</b> "Search ZFS pools", "Read pool status" (health and
read/write/checksum errors), "Start / resume scrub …", "Stop scrub …" and "Stop
monitoring only" (the scrub keeps running in the kernel), with progress and
TXT/HTML/CSV reports. Scrub verifies data
checksums online and may automatically repair damage from redundant copies.
Requires zfsutils-linux and ZFS kernel support. Only imported pools are offered.
Scrub/resilver already in
progress, cancelled scans and old results are not treated as successful new tests.
A scrub reads the whole pool – plan hours for large pools, preferably outside
working hours.</p>
<p>The Filesystem tab provides a read-only <b>status diagnosis for mounted filesystems</b>,
including ext2/3/4, Btrfs, XFS, FAT/exFAT and NTFS: mount state, space and available
error indications. It is not a complete integrity check.</p>
<p>Full integrity checking for <b>offline</b> filesystems: e2fsck -f -n (ext2/3/4),
xfs_repair -n (XFS), btrfs check --readonly --check-data-csum (Btrfs, incl. data checksums), fsck.fat -n (vfat), fsck.exfat -n (exFAT)
and unsquashfs -pf /dev/null (squashfs, reads all data blocks). Every check is strictly read-only.
<b>NTFS</b>: ntfsfix -n performs a read-only <i>basic</i> check only (boot sector, MFT/MFT mirror, dirty flag,
volume start-up); Linux has no full NTFS structure check – for that run <code>chkdsk /f</code> under Windows.
<b>VMFS</b> (VMware ESXi datastores, VMFS3/5/6): read-only metadata check with fsck.vmfs6 or fsck.vmfs
(packages vmfs6-tools/vmfs-tools): inodes, block allocation and directories; reports lost/unallocated blocks,
undefined/orphaned inodes and directory errors. Data blocks are not read. Limits: the tools are not from VMware
and not complete; VMFS-L (ESXi 7+ OSData), datastores with several extents and unknown versions may fail to open
(result "health unknown"). Repair only with VMware VOMA on ESXi (<code>voma -m vmfs -f check|fix -d /vmfs/devices/disks/…:N</code>).
ESXi boot banks (vfat, BOOTBANK1/2) keep their label only in the boot sector; fsck.fat reports this,
but it is not an error.
Mounted filesystems are blocked for integrity checking; use a live USB for the system partition.
<b>The program never repairs filesystems.</b> Repairs change metadata and may remove data; they remain a deliberate
administrator decision on the command line. The live image ships the tools for it: e2fsck (ext2/3/4), xfs_repair (XFS;
if XFS reports a dirty log, mount and cleanly unmount once instead of using -L), btrfs check --repair / btrfs scrub (Btrfs),
fsck.fat (vfat), ntfsfix (NTFS), fsck.exfat (exFAT), zpool scrub (ZFS), gdisk and testdisk (partition tables).
Back up first; with failing hardware, create an image before attempting any repair.
Unsupported filesystems are listed but not changed.</p>
<h2>Tab: ProLiant Info</h2>
<p><b>Usage:</b> "Read now" refreshes all pages; "Show HTML report" opens the report
directly in the browser (like the monitoring tab), "Export as HTML …" saves it to a
file. The sudo password is asked once.</p>
<p>Reads directly from the HPE iLO via ipmitool: system data (model, serial number, BIOS, iLO firmware), all sensors (temperatures, fans, power supplies, voltages) and the complete event log (IPMI SEL), plus the Integrated Management Log (IML) via iLOrest with HPE plain-text messages; all with rating (red = error, orange = warning), filter, search and HTML export. The <i>Memory / ECC</i> page shows the ECC type, modules, kernel EDAC counters and memory events. On a ProLiant or Synergy compute module (e.g. Synergy 480 Gen10) the tab reads automatically at startup and every 5 minutes (administrator password asked once). If no ProLiant/Synergy is detected, a notice is shown and the iLO information is inactive; it can still be read manually (e.g. other servers with IPMI). Requires <code>ipmitool</code>, <code>dmidecode</code> and for the IML <code>ilorest</code> (iLO option “Require Host Authentication” must be off). Long-term monitoring additionally records fan speeds and ECC events via IPMI.</p>
<p><b>RAID page:</b> HPE Smart Array controllers (<code>ssacli</code>/<code>hpssacli</code>) and Broadcom MegaRAID “MR” controllers (<code>storcli64</code>/<code>storcli2</code>) are evaluated in a structured way within the same root session: controller status, cache and battery/capacitor, temperatures, logical drives (RAID level, status, rebuild progress, OS device) and physical drives (position, type, size, status, model, serial, firmware, temperature, power-on hours, SSD life, link speed). Rating: red = controller/drive failed, predictive failure, SSD life below 10&nbsp;% or missing/failed battery with cache present; orange = rebuild/recovery, cache disabled, medium errors, temperature ≥ 60&nbsp;°C (drive) or ≥ 85&nbsp;°C (controller), SSD life below 25&nbsp;%. The raw tool output is shown below the tables; <i>Save diagnostic report (ssaducli)</i> writes the full HPE Smart Storage diagnostic ZIP for HPE support. The RAID rating is part of the overview, summary and HTML export. The RAID button in the disk tabs jumps to this page. The test plan takes a snapshot of RAID, IML and sensors before and after the tests and reports the step <i>ProLiant / RAID controller</i>: it fails only on a new RAID error; new IML entries and sensor warnings are listed as warnings.</p>
<h2>Tab: Long-term monitoring</h2>
<p><b>Options:</b> sampling interval (default 10 s), duration in hours (0 = until
stopped), temperature limit (default 100 °C), log folder (default
"Downloads") and the option "Stop running app
stress tests on temperature limit / new uncorrectable ECC errors" (default on).
"Start monitoring", "Stop and finalize log", "Open report".
<b>Useful</b> alongside long individual tests, for overnight/weekend runs and for
hunting sporadic temperature/ECC problems; <b>not useful</b> without adjusted limits –
100 °C is too high for SSDs and chipsets.</p>
<p><b>Fans:</b> speeds come from hwmon (<code>sensors</code>) or IPMI. If neither reports
fans, ACPI fan cooling devices (<code>/sys/class/thermal/cooling_device*</code>, type
"Fan") are recorded as <code>fan.acpi.N.percent</code>: fan stage in percent of the
maximum (usually 0 or 100 %), no RPM. Typical for desktop boards without a Super-I/O
driver (e.g. Lenovo IdeaCentre). If that is missing too, no fan is monitored – check in
the BIOS or by ear.</p>
<p><b>Individual temperature limits:</b> Only checked sensors override the default.
Limits are saved per user and shared with test-plan monitoring. Follow hardware
specifications; recheck exact sensor keys after hardware changes. Missing sensors
are not monitored. Editing is disabled during a run.</p>
<p><b>Charts:</b> Select a live series. Compacted time buckets preserve measured
min/max and sample-weighted means. Missing data break lines, not become zero.
Elapsed time is independent of wall-clock changes. Up to 256 histories of 240
buckets and up to 24 embedded HTML charts keep memory/report sizes bounded.
All recorded metrics remain in CSV; no external chart library or internet needed.
SMART time-series acquisition is not included yet.</p>
<p>Records available sensors, EDAC/ext4 counters, NVIDIA data and new kernel errors.
CSV is written continuously; stopping creates compact text/HTML summaries.
The default 100 °C limit applies unless individually overridden: adjust it for the hardware,
especially SSDs. Missing sensors/journal access leave gaps in coverage.
This does not replace hardware thermal protection.</p>
<p>The optional safety guard stops this app's stress tests on the temperature limit,
new uncorrectable ECC errors or monitoring failure. It never interrupts filesystem
repairs or firmware self-tests. New individual tests stay locked until monitoring
ends. Test plans offer accompanying monitoring and include the result in their report.</p>
<h2>HPE-specific features</h2>
<p>The RAID evaluation (ProLiant Info → RAID) requires a compatible HPE Smart Array or
MegaRAID controller and its optional tools (<code>ssacli</code>, <code>storcli64</code>, optionally <code>ssaducli</code>). It is not required for ordinary PCs. Remote iLO/Redfish diagnostics
and power supply redundancy checks are not included yet.</p>
""")
        else:
            self._content.setText(_HELP_DE.replace("__VERSION__", __version__) + """
<h2>Tab: Dateisystem (Partitionen, ZFS-Pools)</h2>
<p><b>Dateisystem → Partitionen:</b> „Laufwerke aktualisieren“ listet Partitionen mit
Dateisystem und Einhängepunkt. „Statusdiagnose (eingehängt)“ ist für laufende Systeme
gedacht, „Struktur prüfen (offline)“ nur für ausgehängte Dateisysteme. Berichte landen
im „Protokollordner …“ („HTML-Protokoll öffnen“). Der Tab ist gesperrt, solange andere
Tests laufen („Zuerst die anderen Tests beenden“) – Prüfung und Last gleichzeitig
verfälschen beides. <b>Sinnvoll</b> nach Stromausfällen, vor Datenmigrationen und
nach Lesetests mit Fehlern; <b>nicht sinnvoll</b> als Ersatz für ein Backup.</p>
<p><b>Dateisystem → ZFS-Pools:</b> „ZFS-Pools suchen“, „Pool-Status lesen“ (Zustand und
Lese-/Schreib-/Prüfsummenfehler), „Scrub starten / fortsetzen …“, „Scrub stoppen …“ und
„Nur Überwachung beenden“ (der Scrub läuft im Kernel weiter), dazu Fortschritt und
Text-/HTML-/CSV-Bericht.
Ein Scrub prüft Datenprüfsummen online und kann Schäden mit redundanten Kopien
automatisch korrigieren. Benötigt zfsutils-linux und ZFS-Kernelunterstützung.
Nur importierte Pools werden angeboten. Vorhandene Scrubs/Resilver, abgebrochene Läufe und
alte Ergebnisse werden nicht als erfolgreicher neuer Test gewertet. Ein Scrub liest den
ganzen Pool – bei großen Pools Stunden einplanen und möglichst außerhalb der Nutzungszeit.</p>
<p>Der Dateisystem-Tab bietet eine lesende <b>Statusdiagnose für eingehängte Dateisysteme</b>,
auch ext2/3/4, Btrfs, XFS, FAT/exFAT und NTFS: Einhängezustand, Speicherplatz und verfügbare
Fehlerhinweise. Dies ist keine vollständige Strukturprüfung.</p>
<p>Vollständige Strukturprüfung für <b>ausgehängte</b> Dateisysteme: e2fsck -f -n (ext2/3/4),
xfs_repair -n (XFS), btrfs check --readonly --check-data-csum (Btrfs, inkl. Datenprüfsummen), fsck.fat -n (vfat), fsck.exfat -n (exFAT)
und unsquashfs -pf /dev/null (squashfs, liest alle Datenblöcke). Jede Prüfung ist strikt lesend.
<b>NTFS</b>: ntfsfix -n führt nur eine lesende <i>Basisprüfung</i> durch (Bootsektor, MFT/MFT-Spiegel, Dirty-Flag,
Volume-Start); eine vollständige NTFS-Strukturprüfung gibt es unter Linux nicht – dafür unter Windows
<code>chkdsk /f</code> ausführen.
<b>VMFS</b> (VMware-ESXi-Datenspeicher, VMFS3/5/6): lesende Metadatenprüfung mit fsck.vmfs6 bzw. fsck.vmfs
(Pakete vmfs6-tools/vmfs-tools): Inodes, Blockzuordnung und Verzeichnisse; gemeldet werden verlorene/nicht
zugeordnete Blöcke, undefinierte/verwaiste Inodes und Verzeichnisfehler. Datenblöcke werden nicht gelesen.
Grenzen: Die Werkzeuge stammen nicht von VMware und sind unvollständig; VMFS-L (OSData ab ESXi 7), Datenspeicher
aus mehreren Extents und unbekannte Versionen lassen sich ggf. nicht öffnen (Ergebnis „keine Zustandsaussage“).
Reparatur nur mit VMware VOMA unter ESXi (<code>voma -m vmfs -f check|fix -d /vmfs/devices/disks/…:N</code>).
ESXi-Bootbanks (vfat, BOOTBANK1/2) tragen ihr Label nur im Bootsektor; fsck.fat meldet das, es ist aber
kein Fehler.
Für die Strukturprüfung bleiben eingehängte Dateisysteme gesperrt; für die Systempartition ein Live-USB verwenden.
<b>Das Programm repariert keine Dateisysteme.</b> Reparaturen verändern Metadaten und können Daten entfernen; sie bleiben
eine bewusste Administrator-Entscheidung auf der Kommandozeile. Das Live-Image bringt die Werkzeuge dafür mit: e2fsck (ext2/3/4),
xfs_repair (XFS; bei nicht leerem Journal einmal einhängen und sauber aushängen statt -L), btrfs check --repair / btrfs scrub (Btrfs),
fsck.fat (vfat), ntfsfix (NTFS), fsck.exfat (exFAT), zpool scrub (ZFS), gdisk und testdisk (Partitionstabellen).
Vorher sichern; bei Hardwaredefekten zuerst ein Abbild anlegen.
Andere Dateisysteme werden angezeigt, aber nicht verändert.</p>
<h2>Tab: ProLiant Info</h2>
<p><b>Bedienung:</b> „Jetzt auslesen“ aktualisiert alle Seiten; „HTML-Bericht anzeigen“
öffnet den Bericht direkt im Browser (wie beim Monitoring), „Als HTML exportieren …“
speichert ihn als Datei. Sudo-Passwort wird einmal abgefragt.</p>
<p>Liest per ipmitool direkt vom HPE iLO: Systemdaten (Modell, Seriennummer, BIOS, iLO-Firmware), alle Sensoren (Temperaturen, Lüfter, Netzteile, Spannungen) und das komplette Ereignisprotokoll (IPMI SEL), außerdem über iLOrest das Integrated Management Log (IML) mit HPE-Klartextmeldungen; alles mit Bewertung (rot = Fehler, orange = Warnung), Filter, Suche und HTML-Export. Die Seite <i>Speicher / ECC</i> zeigt ECC-Typ, Module, Kernel-EDAC-Zähler und Speicherereignisse. Auf einem ProLiant oder Synergy-Compute-Modul (z. B. Synergy 480 Gen10) liest der Tab beim Start automatisch und danach alle 5 Minuten aus (einmalige Passwortabfrage). Wird kein ProLiant/Synergy erkannt, erscheint ein Hinweis und die iLO-Informationen sind nicht aktiv; manuelles Auslesen bleibt möglich (z. B. andere Server mit IPMI). Benötigt <code>ipmitool</code>, <code>dmidecode</code> und für das IML <code>ilorest</code> (iLO-Option „Host-Authentifizierung erforderlich“ muss aus sein). Das Langzeitmonitoring zeichnet zusätzlich Lüfterdrehzahlen und ECC-Ereignisse über IPMI auf.</p>
<p><b>Seite RAID:</b> HPE-Smart-Array-Controller (<code>ssacli</code>/<code>hpssacli</code>) und Broadcom-MegaRAID-„MR“-Controller (<code>storcli64</code>/<code>storcli2</code>) werden in derselben Root-Sitzung strukturiert ausgewertet: Controller-Status, Cache und Akku/Kondensator, Temperaturen, logische Laufwerke (RAID-Level, Status, Rebuild-Fortschritt, OS-Gerät) und physische Laufwerke (Position, Typ, Größe, Status, Modell, Seriennummer, Firmware, Temperatur, Betriebsstunden, SSD-Restlebensdauer, Link-Geschwindigkeit). Bewertung: rot = Controller/Laufwerk ausgefallen, Predictive Failure, SSD-Rest unter 10&nbsp;% oder fehlender/defekter Akku bei vorhandenem Cache; orange = Rebuild/Recovery, Cache deaktiviert, Medium-Fehler, Temperatur ≥ 60&nbsp;°C (Laufwerk) bzw. ≥ 85&nbsp;°C (Controller), SSD-Rest unter 25&nbsp;%. Die Rohausgabe der Werkzeuge steht unter den Tabellen; <i>Diagnosebericht (ssaducli) speichern</i> erzeugt das vollständige HPE-Smart-Storage-Diagnose-ZIP für den HPE-Support. Die RAID-Ampel fließt in Übersicht, Zusammenfassung und HTML-Export ein. Der RAID-Button in den Disk-Tabs springt auf diese Seite. Der Gesamttest nimmt vor und nach den Tests eine Momentaufnahme von RAID, IML und Sensoren und berichtet den Schritt <i>ProLiant / RAID-Controller</i>: Er schlägt nur bei einem neuen RAID-Fehler fehl; neue IML-Einträge und Sensorwarnungen werden als Warnung aufgeführt.</p>
<h2>Tab: Langzeitmonitoring</h2>
<p><b>Optionen:</b> Messintervall (Standard 10 s), Dauer in Stunden (0 = bis zum
Stoppen), Temperaturgrenze (Standard 100 °C), Protokollordner (Standard
„Downloads“) und die Option „Bei Temperaturgrenze/neuen
unkorrigierbaren ECC-Fehlern laufende App-Stresstests stoppen“ (Standard an).
„Monitoring starten“, „Stoppen und Protokoll abschließen“, „Bericht öffnen“.
<b>Sinnvoll</b> parallel zu langen Einzeltests, für Nacht-/Wochenendläufe und zur
Suche nach sporadischen Temperatur-/ECC-Problemen; <b>nicht sinnvoll</b> ohne
angepasste Grenzen – 100 °C ist für SSDs und Chipsätze zu hoch.</p>
<p><b>Lüfter:</b> Drehzahlen kommen aus hwmon (<code>sensors</code>) oder IPMI. Melden
beide keine Lüfter, werden ACPI-Lüfter-Kühlgeräte (<code>/sys/class/thermal/
cooling_device*</code>, Typ „Fan“) als <code>fan.acpi.N.percent</code> erfasst:
Lüfterstufe in Prozent des Maximums (meist 0 oder 100 %), keine Drehzahl. Typisch
für Desktop-Boards ohne Super-I/O-Treiber (z. B. Lenovo IdeaCentre). Fehlt auch
das, ist kein Lüfter überwacht – dann im BIOS oder per Gehör kontrollieren.</p>
<p><b>Individuelle Temperaturgrenzen:</b> Nur aktivierte Sensorzeilen überschreiben
den Standardwert. Grenzen werden pro Benutzer gespeichert und auch im Gesamttest
genutzt. Herstellerangaben beachten; nach Hardwarewechsel exakte Sensorschlüssel
erneut kontrollieren. Fehlende Sensoren werden nicht überwacht. Während eines
Laufs sind die Grenzen nicht veränderbar.</p>
<p><b>Diagramme:</b> Live-Messreihe wählen. Verdichtete Zeitabschnitte erhalten
gemessene Min/Max und nach Messanzahl gewichtete Mittelwerte. Fehlende Werte
unterbrechen Linien, statt als Null zu erscheinen. Verstrichene Zeit bleibt von
Systemuhränderungen unabhängig. Maximal 256 Messreihen mit 240 Abschnitten und
24 eingebettete HTML-Diagramme begrenzen Speicher und Bericht; CSV erfasst alle
Messwerte. Keine externe Chart-Bibliothek oder Internet nötig. SMART-Verläufe
sind noch nicht enthalten.</p>
<p>Erfasst verfügbare Sensoren, EDAC-/ext4-Zähler, NVIDIA-Werte und neue Kernel-Fehler.
CSV wird fortlaufend geschrieben; beim Stoppen entstehen kompakte Text-/HTML-Berichte.
Die vorbelegte Grenze von 100 °C gilt ohne individuelle Überschreibung und muss an die Hardware angepasst
werden, insbesondere bei SSDs. Fehlende Sensoren/Journalrechte bedeuten Überwachungslücken.
Kein Ersatz für den thermischen Schutz der Hardware.</p>
<p>Der optionale Wächter stoppt App-Stresstests bei Temperaturgrenze, neuen unkorrigierbaren
ECC-Fehlern oder Monitoring-Ausfall. Dateisystemreparaturen und Firmware-Selbsttests werden
nicht unterbrochen. Neue Einzeltests bleiben bis zum Beenden des Monitorings gesperrt.
Der Gesamttestplan bietet Begleitmonitoring mit Ergebnis im gemeinsamen Bericht.</p>
<h2>HPE-spezifische Funktionen</h2>
<p>Die RAID-Auswertung (ProLiant Info → RAID) benötigt einen passenden HPE-Smart-Array- oder
MegaRAID-Controller und die optionalen Werkzeuge (<code>ssacli</code>, <code>storcli64</code>, optional <code>ssaducli</code>). Für normale PCs sind sie nicht erforderlich. Remote-iLO/Redfish
und Netzteilredundanz-Prüfungen sind noch nicht enthalten.</p>
""")
