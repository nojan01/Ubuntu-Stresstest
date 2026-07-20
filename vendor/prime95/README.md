# vendor/prime95

Dieses Verzeichnis enthält **Prime95/mprime** (Linux 64-Bit) für Torture-Tests.

## Inhalt

| Datei           | Beschreibung                          |
|-----------------|---------------------------------------|
| `mprime`        | Prime95 Linux CLI Binary (~37 MB)     |
| `libgmp.so*`    | GNU MP Bignum Library (Abhängigkeit)  |
| `*.txt`         | Dokumentation / Konfiguration         |

## Installation

Die Binary wird automatisch installiert durch:

```bash
sudo bash scripts/install_hardwaretest.sh
```

Oder manuell:

```bash
cd vendor/prime95
wget https://www.mersenne.org/ftp_root/gimps/p95v308b17.linux64.tar.gz
tar -xzf p95v308b17.linux64.tar.gz
chmod +x mprime
rm p95v308b17.linux64.tar.gz
```

## Suchpfade der App

Das Programm sucht `mprime` in dieser Reihenfolge:

1. `<projekt>/vendor/prime95/mprime` (gebündelt – **bevorzugt**)
2. `~/Prime95/mprime` (Legacy-Pfad)
3. `mprime` im System-PATH

## Lizenz

Prime95 / mprime ist Copyright © GIMPS (Great Internet Mersenne Prime Search).
Siehe `license.txt` im selben Verzeichnis.
