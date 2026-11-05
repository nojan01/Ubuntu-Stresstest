# Portierung nach AlmaLinux / RHEL-Derivate

Stand: Hardwaretest 0.2.69, hwtest-live 2026.11.04d. Einschätzung, was für einen
Betrieb auf AlmaLinux 9/10 (RHEL, Rocky, Oracle Linux) nötig wäre.

## 1. Hardwaretest-App (Aufwand: gering bis mittel)

Der Python/PySide6-Code ist distributionsneutral. Werkzeuge werden über `shutil.which`
gesucht, HPE-/Broadcom-Tools über feste `/opt`-Pfade (identisch auf RHEL).

| Bereich | Befund | Aufgabe |
|---|---|---|
| AppImage | Bündelt Python + Qt; braucht nur glibc ≥ 2.28, FUSE 2/3, libxcb, libGL | Auf AlmaLinux 9 testen (`dnf install fuse fuse-libs libxcb libxkbcommon-x11 mesa-libGL`) |
| Paket | Nur `.deb` (`scripts/build_deb.sh`) | RPM-Spec oder `fpm -s dir -t rpm` aus dem gleichen PyInstaller-Bundle; Depends übersetzen (s. u.) |
| Hinweistexte | ~10 Stellen „sudo apt install …“ (`system_info.py`, `proliant_panel.py`, `network_panel.py`, `info_panel.py`, `filesystem_check.py`, `zfs.py`, `test_panel.py`) | Zentrale Funktion `install_hint(pkg)` die je nach `/etc/os-release` `apt`/`dnf` ausgibt |
| memtest86+ | `dpkg -s memtest86+` in `system_info.py:688` | zusätzlich `rpm -q memtest86+` |
| Terminal-Start | `x-terminal-emulator` (Debian-Alias) nur als Fallback; gnome-terminal/xfce4-terminal/konsole werden direkt gesucht | nichts |
| Update-Prüfung | `update_check.py` erkennt `.AppImage`/`.deb` | `.rpm` ergänzen, sobald es ein RPM gibt |
| sudoers-Dateien | `/etc/sudoers.d/hardwaretest-*` (vom .deb) | identisch im RPM mitliefern (`%files`, `%config`) |
| pkexec/polkit | `polkitd` Paketnamen unterschiedlich | RPM-Depends: `polkit` |
| OpenDCDiag | eigener Build (`build_opendcdiag.sh`) | auf Alma neu bauen (gcc/meson vorhanden) |

### Paketnamen Debian → RHEL/EPEL

| Debian | AlmaLinux (Repo) |
|---|---|
| stress-ng | stress-ng (EPEL) |
| fio | fio (AppStream) |
| btop | btop (EPEL) |
| smartmontools, nvme-cli, dmidecode, pciutils, lshw, iperf3, ipmitool, jq, curl | gleichnamig (BaseOS/AppStream/EPEL) |
| vulkan-tools, libvulkan1, mesa-vulkan-drivers | vulkan-tools, vulkan-loader, mesa-vulkan-drivers |
| fastfetch | fastfetch (EPEL 9+) |
| memtest86+ | memtest86+ (BaseOS, nur BIOS-Boot) |
| zfsutils-linux | zfs (OpenZFS-Repo, kmod oder DKMS) |
| vmfs-tools / vmfs6-tools | nicht paketiert → aus Quellen bauen |
| iputils-ping | iputils |
| libxcb-*, libxkbcommon-x11-0 | libxcb, xcb-util-*, libxkbcommon-x11 |
| policykit-1 / polkitd | polkit |
| ilorest, ssacli, ssaducli, MFT (mst), storcli | von HPE/NVIDIA/Broadcom **offiziell als RPM** – auf RHEL sogar besser unterstützt |
| nvidia-driver, datacenter-gpu-manager | NVIDIA-CUDA-Repo für rhel9 (dnf module) |

## 2. Live-ISO (Aufwand: hoch – Neuaufbau)

Das ISO nutzt Debian `live-build`: `live/package-lists/*.list.chroot`, `live/hooks/live/*.hook.chroot`,
`live/includes.chroot/…`, Persistenz über `live-boot` (`persistence`-Partition). Nichts davon
existiert unter RHEL.

| Debian live-build | RHEL-Äquivalent |
|---|---|
| `lb config` / `lb build` | `livemedia-creator` (Lorax) oder `mkksiso`/`kiwi-ng` |
| `package-lists/*.list.chroot` | `%packages`-Abschnitt in einer Kickstart-Datei |
| `hooks/live/*.hook.chroot` | `%post`-Skripte in Kickstart |
| `includes.chroot/` | `%post` mit heredocs oder zusätzliches RPM |
| `live-boot` Persistenz (`persistence.conf`) | `livesys` + `overlay`/`rd.live.overlay` (Dracut) – andere Semantik, keine `/home`-Union per Label |
| `openbox` + `tint2` + `pcmanfm` + `surf` | gleich (EPEL), `surf` fehlt → `falkon` oder `epiphany` |
| `btop`-Backport, `fuse3`, `zip` | EPEL/BaseOS |
| Debian-Firmware-Pakete (`firmware-*`) | `linux-firmware` (eine Datei, vollständig) |
| ZFS via DKMS in `0500-hpe-live-dkms` | OpenZFS kmod-Repo (`zfs-kmod`), passend zum Kernel |
| NVIDIA .run/Repo im Hook | NVIDIA rhel9-Repo mit `kmod-nvidia-latest-dkms` + DCGM |

Vorteile Alma-ISO: HPE (ssacli, ilorest, AMS, SUM), Broadcom (storcli), NVIDIA (DCGM, Treiber),
Marvell/QLogic (qaucli) liefern RHEL-RPMs offiziell → kein Umpacken von RPM nach DEB wie heute.
Kernel 5.14 (Alma 9) ist älter als Debian trixie 6.12 → neuere Hardware (z. B. Gen12) ggf. nur mit
Alma 10 (Kernel 6.12).

## 3. Empfohlene Reihenfolge

1. AppImage auf AlmaLinux 9 (VM) starten; Tabs durchgehen, Fehlermeldungen notieren.
2. `install_hint()` einführen und `rpm -q` ergänzen (kleiner Commit, kein Risiko für Debian).
3. RPM-Paket via `fpm` aus `build/portable/pyinstaller/` erzeugen; sudoers + Desktop-Datei mitliefern.
4. Erst danach entscheiden, ob ein Alma-Live-ISO (Kickstart + Lorax) gebaut wird oder das Debian-ISO
   als neutrales Testmedium bleibt.
