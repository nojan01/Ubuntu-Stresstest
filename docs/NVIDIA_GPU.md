# NVIDIA-GPU-Diagnose unter Ubuntu

Diese Anleitung gilt für Hardwaretest auf Ubuntu 24.04 LTS und Ubuntu 26.04 LTS.
Die NVIDIA-Paketquelle und die zu installierende DCGM-Variante müssen zur
Ubuntu- und Treiberversion passen.

## 1. Vorhandene Hardware und Treiber prüfen

```bash
lspci -nnk | grep -A3 -Ei 'VGA|3D|NVIDIA'
lsmod | grep nvidia
nvidia-smi
```

Wenn die NVIDIA-GPU in `lspci` erscheint, `nvidia-smi` aber fehlt oder einen
Fehler meldet, ist der Treiber nicht oder nicht korrekt installiert.

## 2. NVIDIA-Treiber installieren

Ubuntu kann den für die erkannte Karte empfohlenen Treiber bestimmen:

```bash
sudo apt update
sudo apt install ubuntu-drivers-common
ubuntu-drivers devices
sudo ubuntu-drivers install
sudo reboot
```

Nach dem Neustart muss `nvidia-smi` alle GPUs ohne Fehler auflisten. Auf
HPE-Systemen mit Secure Boot muss das bei der Installation erzeugte
MOK-Zertifikat beim Neustart bestätigt werden. Andernfalls kann das
NVIDIA-Kernelmodul trotz installiertem Paket nicht geladen werden.

## 3. Offizielle NVIDIA-Paketquelle einrichten

Für Ubuntu 24.04 auf x86-64:

```bash
wget https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2404/x86_64/cuda-keyring_1.1-1_all.deb -O /tmp/cuda-keyring_1.1-1_all.deb
sudo dpkg -i /tmp/cuda-keyring_1.1-1_all.deb
sudo apt update
```

Für Ubuntu 26.04 auf x86-64:

```bash
wget https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2604/x86_64/cuda-keyring_1.1-1_all.deb -O /tmp/cuda-keyring_1.1-1_all.deb
sudo dpkg -i /tmp/cuda-keyring_1.1-1_all.deb
sudo apt update
```

## 4. Passende DCGM-Version installieren

Zuerst die vom Treiber unterstützte CUDA-Hauptversion ablesen:

```bash
nvidia-smi -q | grep -E 'Driver Version|CUDA Version'
```

Bei CUDA 12.x:

```bash
sudo apt install --install-recommends datacenter-gpu-manager-4-cuda12
```

Bei CUDA 13.x:

```bash
sudo apt install --install-recommends datacenter-gpu-manager-4-cuda13
```

Maxwell-, Pascal- und Volta-GPUs mit Treiber 580 sollen laut NVIDIA weiterhin
das CUDA-12-DCGM-Paket verwenden. Der vollständige CUDA-Compiler oder das CUDA
Toolkit wird für Hardwaretest nicht benötigt.

## 5. DCGM starten und prüfen

```bash
sudo systemctl enable --now nvidia-dcgm
systemctl status nvidia-dcgm --no-pager
dcgmi discovery -l
```

`dcgmi discovery -l` muss die erwartete Anzahl GPUs anzeigen. Danach stehen im
Hardwaretest-Tab **NVIDIA GPU** die aktiven DCGM-Diagnosen zur Verfügung.

## 6. Bedeutung der Teststufen

- Stufe 1: kurze Software- und Einsatzbereitschaftsprüfung.
- Stufe 2: zusätzlich PCIe, GPU-Speicher und Speicherbandbreite.
- Stufe 3: längere Hardware-, Last- und Leistungsdiagnose.
- Stufe 4: erweiterte Langzeitdiagnose einschließlich intensiver Speichertests,
  soweit von GPU und DCGM-Version unterstützt.

Vor Stufe 2 bis 4 sollten produktive CUDA-, AI- und VDI-Arbeiten beendet sein.
Für GeForce- und viele Quadro-Karten ist nur Stufe 1 vollständig unterstützt.

## 7. Fehlerdiagnose

```bash
journalctl -k -b | grep -Ei 'nvidia|nvrm|xid'
journalctl -u nvidia-dcgm -b --no-pager
ls -l /dev/nvidia*
dkms status
```

Häufige Ursachen sind ein nach einem Kernel-Update nicht gebautes DKMS-Modul,
ein unter Secure Boot nicht bestätigter MOK-Schlüssel, eine nicht zur
Treiberversion passende DCGM-Ausgabe oder fehlender PCIe-Passthrough in einer VM.

## Offizielle Dokumentation

- NVIDIA DCGM: https://docs.nvidia.com/datacenter/dcgm/latest/user-guide/getting-started.html
- NVIDIA CUDA-Paketquelle: https://docs.nvidia.com/cuda/cuda-installation-guide-linux/
