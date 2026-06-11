# Netzwerk für DHCP konfigurieren (Ubuntu Server / CLI)

Ubuntu Server verwendet **Netplan** als Netzwerk-Konfigurationstool. Die Konfigurationsdateien liegen unter `/etc/netplan/`.

---

## 1. Netzwerk-Interface ermitteln

```bash
ip link show
```

Typische Namen: `eth0`, `ens18`, `enp0s3`, etc. Merke dir den Interface-Namen.

---

## 2. Bestehende Netplan-Konfiguration prüfen

```bash
ls /etc/netplan/
```

Meist existiert eine Datei wie `00-installer-config.yaml` oder `50-cloud-init.yaml`.

---

## 3. Konfigurationsdatei bearbeiten

```bash
sudo nano /etc/netplan/00-installer-config.yaml
```

Inhalt für **DHCP** (ersetze `ens18` durch deinen Interface-Namen):

```yaml
network:
  version: 2
  renderer: networkd
  ethernets:
    ens18:
      dhcp4: true
      dhcp6: false
```

### Parameter erklärt

| Parameter           | Bedeutung                                    |
| ------------------- | -------------------------------------------- |
| `version: 2`       | Netplan-Konfigurationsversion                |
| `renderer: networkd`| Verwendet `systemd-networkd` als Backend    |
| `dhcp4: true`      | IPv4-Adresse per DHCP beziehen               |
| `dhcp6: false`     | IPv6-DHCP deaktiviert (auf `true` setzen falls gewünscht) |

---

## 4. Erweiterte DHCP-Optionen (optional)

```yaml
network:
  version: 2
  renderer: networkd
  ethernets:
    ens18:
      dhcp4: true
      dhcp4-overrides:
        use-dns: true
        use-routes: true
        route-metric: 100
      nameservers:
        addresses:
          - 8.8.8.8
          - 1.1.1.1
      optional: true
```

- **`dhcp4-overrides`** – Feinsteuerung, was vom DHCP-Server übernommen wird
- **`nameservers`** – Eigene DNS-Server zusätzlich/alternativ setzen
- **`optional: true`** – Boot wartet nicht auf das Interface (nützlich bei optionalen NICs)

---

## 5. Konfiguration validieren und anwenden

```bash
# Syntax prüfen
sudo netplan generate

# Änderungen anwenden
sudo netplan apply
```

Falls etwas schiefgeht, kannst du mit `try` testen – die Änderung wird nach 120 Sekunden automatisch zurückgesetzt, wenn du nicht bestätigst:

```bash
sudo netplan try
```

---

## 6. Ergebnis prüfen

```bash
# IP-Adresse anzeigen
ip addr show ens18

# Gateway prüfen
ip route

# DNS prüfen
resolvectl status

# Konnektivität testen
ping -c 4 8.8.8.8
ping -c 4 google.com
```

---

## 7. Fehlerbehebung

```bash
# Netzwerkdienst-Status prüfen
sudo systemctl status systemd-networkd

# DHCP-Lease manuell erneuern
sudo dhclient -r ens18 && sudo dhclient ens18

# Netzwerkdienst neu starten
sudo systemctl restart systemd-networkd

# Logs prüfen
journalctl -u systemd-networkd --no-pager -n 50
```

---

> **Wichtig:** YAML ist einrückungssensitiv – immer **Leerzeichen** (keine Tabs) verwenden, jeweils **2 Leerzeichen** pro Ebene.
