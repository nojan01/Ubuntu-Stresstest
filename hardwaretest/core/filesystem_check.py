"""Offline ext filesystem checks. Revalidate the device inside the privileged worker."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

from hardwaretest.core.paths import self_command_for_root


# VMware ESXi-Datenspeicher; blkid meldet je nach Lage VMFS oder VMFS_volume_member.
VMFS_TYPES = {"VMFS", "VMFS_volume_member"}
NTFS_TYPES = {"ntfs", "ntfs3"}
CHECKABLE_TYPES = {"ext2", "ext3", "ext4", "xfs", "vfat", "squashfs", "btrfs", *NTFS_TYPES, *VMFS_TYPES}
# Das Programm prüft Dateisysteme ausschließlich lesend. Reparaturen (e2fsck -p,
# xfs_repair, fsck.fat -a, btrfs check --repair …) verändern Metadaten und können
# Daten entfernen – sie bleiben bewusst dem Administrator auf der Kommandozeile
# vorbehalten; die Werkzeuge dafür liegen dem Live-Image bei.

_TOOLS = {
    "e2fsck": ("/usr/sbin/e2fsck", "/sbin/e2fsck", "e2fsprogs"),
    "xfs_repair": ("/usr/sbin/xfs_repair", "/sbin/xfs_repair", "xfsprogs"),
    "fsck.fat": ("/usr/sbin/fsck.fat", "/sbin/fsck.fat", "dosfstools"),
    "unsquashfs": ("/usr/bin/unsquashfs", "/usr/sbin/unsquashfs", "squashfs-tools"),
    "fsck.vmfs6": ("/usr/sbin/fsck.vmfs6", "/sbin/fsck.vmfs6", "vmfs6-tools"),
    "fsck.vmfs": ("/usr/sbin/fsck.vmfs", "/sbin/fsck.vmfs", "vmfs-tools"),
    "btrfs": ("/usr/bin/btrfs", "/usr/sbin/btrfs", "btrfs-progs"),
    "ntfsfix": ("/usr/bin/ntfsfix", "/usr/sbin/ntfsfix", "ntfs-3g"),
}

# Ausgabe von fsck.fat, die bei ESXi-Bootbanks (BOOTBANK1/2) immer auftritt: ESXi
# schreibt das Label nur in den Bootsektor. Das ist kein Dateisystemfehler.
_VFAT_BENIGN = re.compile(
    r"^(\s*|fsck\.fat \d.*|Leaving filesystem unchanged\.|\S+: \d+ files, \d+/\d+ clusters"
    r"|Label in boot sector is '.*', but there is no volume label in root directory\."
    r"|\s+Auto-removing label from boot sector\.)$")
_VFAT_LABEL = re.compile(r"^Label in boot sector is '.*', but there is no volume label in root directory\.$")
_VMFS_SUMMARY = re.compile(r"^(Unallocated blocks|Lost blocks|Undefined inodes|Orphaned inodes|Directory errors)\s*:\s*(\d+)\s*$")
_VMFS_MAX_DETAIL_LINES = 200
# ntfsfix -n meldet ein gesetztes Dirty-Flag bzw. Korruption nur im Text; der Exit-Code ist dabei nicht verlässlich.
_NTFS_FINDING = re.compile(r"dirty|scheduled for check|corrupt|FAILED|chkdsk", re.I)


def _tool(name: str) -> str:
    first, second, _package = _TOOLS[name]
    return second if not os.path.exists(first) and os.path.exists(second) else first


def _unsquashfs_full_read() -> bool:
    try:
        result = subprocess.run([_tool("unsquashfs"), "-help"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return "-pf" in (result.stdout + result.stderr)


@dataclass(frozen=True)
class FilesystemDevice:
    path: str
    fstype: str
    uuid: str
    number: str
    mounts: tuple[str, ...]
    kind: str
    children: bool = False

    @property
    def supported(self) -> bool:
        return self.fstype in CHECKABLE_TYPES

    @property
    def checkable_while_mounted(self) -> bool:
        # squashfs ist unveränderlich; unsquashfs liest nur – auch eingehängt sicher (z. B. Snaps, Live-System).
        return self.fstype == "squashfs" and not self.children


def list_filesystems() -> list[FilesystemDevice]:
    result = subprocess.run(
        ["lsblk", "--json", "--paths", "-o", "NAME,TYPE,FSTYPE,UUID,MOUNTPOINTS,MAJ:MIN"],
        capture_output=True, text=True, timeout=15, check=True,
    )
    devices = {}

    def visit(rows):
        for row in rows:
            if row.get("fstype"):
                device = FilesystemDevice(
                    row["name"], row["fstype"], row.get("uuid") or "", row["maj:min"],
                    tuple(m for m in row.get("mountpoints", []) if m), row["type"],
                    bool(row.get("children")),
                )
                devices[device.path] = device
            visit(row.get("children", []))

    visit(json.loads(result.stdout)["blockdevices"])
    return list(devices.values())


def mounted_numbers(contents: str) -> set[str]:
    return {fields[2] for line in contents.splitlines() if len(fields := line.split()) >= 6}


def validate_identity(expected: FilesystemDevice) -> FilesystemDevice:
    """Validate identity for both online status and offline actions."""
    current = next((d for d in list_filesystems() if d.path == expected.path), None)
    if current is None or (current.uuid, current.number, current.fstype) != (
        expected.uuid, expected.number, expected.fstype
    ):
        raise ValueError("Device changed / Gerät wurde geändert. Refresh the list / Liste aktualisieren.")
    info = os.stat(current.path)
    if not stat.S_ISBLK(info.st_mode) or f"{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}" != current.number:
        raise ValueError("Not the selected block device / Nicht das ausgewählte Blockgerät.")
    return current


def validate_device(expected: FilesystemDevice, action: str = "check") -> FilesystemDevice:
    """Fail closed on identity changes, mounts, active layers or unreadable state."""
    current = validate_identity(expected)
    if not current.supported or current.kind not in {"part", "disk", "lvm", "crypt", "loop", "raid0", "raid1", "raid4", "raid5", "raid6", "raid10", "md"}:
        raise ValueError("Only offline ext2/3/4, xfs, btrfs, vfat, ntfs, squashfs, VMFS / Nur ext2/3/4, xfs, btrfs, vfat, ntfs, squashfs, VMFS offline unterstützt.")
    if action == "check" and current.checkable_while_mounted:
        return current
    numbers = mounted_numbers(Path("/proc/self/mountinfo").read_text())
    # Also reject mounts in the host's initial namespace, when different.
    numbers |= mounted_numbers(Path("/proc/1/mountinfo").read_text())
    holders = Path("/sys/dev/block") / current.number / "holders"
    if current.mounts or current.number in numbers or current.children or any(holders.iterdir()):
        raise ValueError("Device mounted or in use / Gerät eingehängt oder in Benutzung. Use a live system.")
    for line in Path("/proc/swaps").read_text().splitlines()[1:]:
        if os.path.realpath(line.split()[0]) == os.path.realpath(current.path):
            raise ValueError("Active swap / Aktiver Auslagerungsspeicher.")
    return current


def checker_command(action: str, device: str, fstype: str = "ext4") -> list[str]:
    """Nur lesende Prüfkommandos; eine Reparatur wird nie erzeugt."""
    if action != "check":
        raise ValueError("Only read-only checks are supported / Nur lesende Prüfung unterstützt.")
    if fstype == "xfs":
        # -n: nur lesen. Niemals -L (verwirft das Journal und damit Daten).
        return [_tool("xfs_repair"), "-n", device]
    if fstype == "vfat":
        return [_tool("fsck.fat"), "-n", device]
    if fstype == "squashfs":
        if _unsquashfs_full_read():
            # -pf liest alle Metadaten UND alle Datenblöcke (inkl. Dekomprimierung).
            return [_tool("unsquashfs"), "-no-progress", "-pf", "/dev/null", device]
        # Ältere squashfs-tools (< 4.6): nur Superblock, Inode- und Verzeichnistabellen.
        return [_tool("unsquashfs"), "-no-progress", "-lls", device]
    if fstype == "btrfs":
        # --readonly: nur lesen; --check-data-csum prüft zusätzlich alle Datenblöcke.
        return [_tool("btrfs"), "check", "--readonly", "--check-data-csum", device]
    if fstype in NTFS_TYPES:
        # -n: nur lesen. Basisprüfung (Bootsektor, MFT/MFT-Spiegel, Dirty-Flag); eine vollständige
        # Strukturprüfung wie chkdsk gibt es unter Linux nicht.
        return [_tool("ntfsfix"), "-n", device]
    if fstype in VMFS_TYPES:
        # fsck.vmfs(6) liest nur und schreibt nie. VMFS6 zuerst, VMFS3/5 als Rückfall.
        return [_tool("fsck.vmfs6"), device]
    if fstype not in {"ext2", "ext3", "ext4"}:
        raise ValueError(f"Unsupported filesystem / Nicht unterstützt: {fstype}")
    # -n is strictly read-only; never run the generic fsck dispatcher on mounted filesystems.
    return [_tool("e2fsck"), "-f", "-n", device]


def worker_command(action: str, device: FilesystemDevice, language: str = "de") -> list[str]:
    program = self_command_for_root()
    return [*program, "--filesystem-worker", action, "--device", device.path,
                      "--uuid", device.uuid, "--number", device.number, "--fstype", device.fstype,
                      "--language", language]


def result_text(code: int, action: str, language: str) -> str:
    def t(de, en):
        return en if language == "en" else de
    if action == "status" and code == 0:
        return t("Statusdiagnose abgeschlossen. Keine vollständige Strukturprüfung; Details und Grenzen siehe Protokoll.",
                 "Status diagnosis completed. Not a full integrity check; see log for findings and coverage.")
    if action == "status" and code == 4:
        return t("Statusdiagnose: Fehlerhinweise gefunden. Siehe Protokoll.",
                 "Status diagnosis: error indications found. See log.")
    if code == 0:
        return t("Prüfung abgeschlossen: keine Fehler gemeldet.", "Check completed: no errors reported.")
    if code >= 0 and code < 8 and code & 4:
        return t("Dateisystemfehler vorhanden. Siehe Protokoll; Reparatur manuell mit dem passenden Werkzeug.",
                 "Filesystem errors found. See log; repair manually with the appropriate tool.")
    return t("Prüfung nicht erfolgreich abgeschlossen – keine Zustandsaussage.",
             "Check did not complete successfully – health unknown.") + f" (Exit: {code})"


def read_online_status(device: FilesystemDevice, language: str = "de") -> tuple[str, int]:
    """Read mounted filesystem telemetry; never run fsck or write test files."""
    def t(de, en):
        return en if language == "en" else de
    lines = [t("Online-Statusdiagnose (nur lesend)", "Online status diagnosis (read-only)"),
             f"{device.path} | {device.fstype} | UUID: {device.uuid}",
             t("Keine vollständige Strukturprüfung. Dafür offline prüfen / Live-USB verwenden.",
               "Not a full integrity check. For that, check offline / use a live USB.")]
    failed = False
    covered = False
    # Actual mounts are matched by major:minor, including bind mounts.
    mounts = []
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        fields = line.split()
        if len(fields) < 10 or "-" not in fields:
            continue
        target = re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), fields[4])
        separator = fields.index("-")
        mount_type = fields[separator + 1]
        mount_types = {device.fstype}
        if device.fstype in NTFS_TYPES:
            mount_types.update({"ntfs", "ntfs3", "fuseblk", "fuse.ntfs-3g"})
        # Btrfs uses a virtual major:minor for subvolume mounts. Fresh lsblk
        # mountpoints still identify the selected filesystem correctly.
        if fields[2] == device.number or (target in device.mounts and mount_type in mount_types):
            mounts.append((target, fields[5]))
    if not mounts:
        raise ValueError("Not mounted / Nicht eingehängt. Refresh / Aktualisieren.")
    for target, options in mounts[:8]:
        lines.append(t("Einhängepunkt", "Mount") + f": {target} ({options})")
        try:
            usage = os.statvfs(target)
            total = usage.f_blocks * usage.f_frsize / (1024 ** 3)
            free = usage.f_bavail * usage.f_frsize / (1024 ** 3)
            lines.append(t("Kapazität / verfügbar", "Capacity / available") + f": {total:.1f} / {free:.1f} GiB")
            covered = True
        except OSError as exc:
            lines.append(t("Speicherdaten nicht verfügbar: ", "Space information unavailable: ") + str(exc))
    kernel_name = (Path("/sys/dev/block") / device.number).resolve().name
    if device.fstype == "ext4":
        counter = Path("/sys/fs/ext4") / kernel_name / "errors_count"
        try:
            count = int(counter.read_text())
            lines.append(t("Vom ext4-Treiber erfasste Dateisystemfehler", "Filesystem errors recorded by the ext4 driver") + f": {count}")
            failed |= count > 0
        except (OSError, ValueError):
            lines.append(t("ext4-Fehlerzähler nicht verfügbar.", "ext4 error counter unavailable."))
    elif device.fstype == "btrfs" and mounts:
        stats_failed, stats_lines = btrfs_device_stats(mounts[0][0], language)
        lines.extend(stats_lines)
        failed |= stats_failed
    elif device.fstype in NTFS_TYPES:
        lines.append(t("NTFS: kein Fehlerzähler im Kernel. Offline-Basisprüfung mit ntfsfix -n möglich; vollständige Prüfung nur unter Windows (chkdsk).",
                       "NTFS: no kernel error counter. Offline basic check via ntfsfix -n; full check only under Windows (chkdsk)."))
    else:
        lines.append(t("Für diesen Typ ist kein ext4-Fehlerzähler verfügbar.", "No ext4 error counter for this filesystem type."))
    try:
        result = subprocess.run(["journalctl", "-k", "-b", "-n", "2000", "--no-pager", "-o", "cat"],
                                capture_output=True, text=True, timeout=15)
        if result.returncode:
            raise OSError(result.stderr.strip() or "journalctl failed")
        names = {Path(device.path).name, kernel_name}
        mentions = re.compile(r"(?<![\w-])(?:" + "|".join(re.escape(n) for n in names) + r")(?![\w-])", re.I)
        errors = re.compile(r"\berrors?\b(?![=])|corrupt|I/O|abort|remount(?:ing|ed).*read-only|fault|\bfail(?:ed|ure)?\b", re.I)
        matches = [line[:2000] for line in result.stdout.splitlines() if mentions.search(line) and errors.search(line)]
        lines.append(t("Kernel: maximal die letzten 2000 Meldungen dieses Systemstarts ausgewertet.",
                       "Kernel: at most the last 2000 messages of this boot inspected."))
        if matches:
            failed = True
            lines.extend(matches[:100])
            if len(matches) > 100:
                lines.append(t("Ausgabe auf 100 Treffer begrenzt.", "Output limited to 100 matches."))
        else:
            lines.append(t("Keine passenden Fehlerhinweise im ausgewerteten Ausschnitt.",
                           "No matching error indications in the inspected excerpt."))
    except (OSError, subprocess.SubprocessError) as exc:
        lines.append(t("Kernelprotokoll nicht verfügbar: ", "Kernel log unavailable: ") + str(exc))
    lines.append(t("Die vollständige Prüfung und Reparatur benötigen ein ausgehängtes Dateisystem.",
                   "Full integrity checking and repair require an unmounted filesystem."))
    return "\n".join(lines), 4 if failed else (0 if covered else 8)


_BTRFS_STAT = re.compile(r"^\[(?P<dev>[^\]]+)\]\.(?P<name>\w+)\s+(?P<value>\d+)\s*$")


def parse_btrfs_device_stats(text: str) -> dict[str, dict[str, int]]:
    """``btrfs device stats`` → {Gerät: {Zähler: Wert}}."""
    stats: dict[str, dict[str, int]] = {}
    for line in text.splitlines():
        match = _BTRFS_STAT.match(line.strip())
        if match:
            stats.setdefault(match["dev"], {})[match["name"]] = int(match["value"])
    return stats


def btrfs_device_stats(mountpoint: str, language: str = "de") -> tuple[bool, list[str]]:
    """Btrfs-Fehlerzähler je Gerät (lesend). True, wenn ein Zähler größer als 0 ist."""
    def t(de, en):
        return en if language == "en" else de
    tool = _tool("btrfs")
    if not os.path.exists(tool):
        return False, [t("btrfs-progs fehlen – keine Gerätestatistik.", "btrfs-progs missing – no device statistics.")]
    try:
        result = subprocess.run([tool, "device", "stats", mountpoint], capture_output=True, text=True,
                                errors="replace", timeout=30, env={**os.environ, "LC_ALL": "C"})
    except (OSError, subprocess.SubprocessError) as exc:
        return False, [t("Btrfs-Gerätestatistik nicht verfügbar: ", "Btrfs device statistics unavailable: ") + str(exc)]
    stats = parse_btrfs_device_stats(result.stdout)
    if not stats:
        return False, [t("Btrfs-Gerätestatistik nicht auswertbar: ", "Btrfs device statistics unreadable: ")
                       + (result.stderr.strip() or f"Exit {result.returncode}")]
    lines = [t("Btrfs-Fehlerzähler je Gerät (btrfs device stats):", "Btrfs error counters per device (btrfs device stats):")]
    failed = False
    for dev, counters in stats.items():
        lines.append(f"  {dev}: " + ", ".join(f"{name}={value}" for name, value in counters.items()))
        failed |= any(counters.values())
    if failed:
        lines.append(t("Fehlerzähler größer 0 – Datenträger und Kabel prüfen; btrfs scrub empfohlen.",
                       "Error counters above 0 – check disks and cabling; btrfs scrub recommended."))
    return failed, lines


def normalize_exit(fstype: str, action: str, code: int, output: str = "") -> int:
    """Rechnet Exit-Codes von xfs_repair -n/fsck.fat -n/unsquashfs in e2fsck-Semantik um."""
    if code < 0:
        return 8
    if fstype == "xfs":
        if code == 0:
            return 0
        return 4 if code == 1 else 8  # 2 = Journal nicht leer, sonst Betriebsfehler
    if fstype == "vfat":
        if code == 0:
            return 0
        return 4 if code == 1 else 8
    if fstype == "squashfs":
        return 0 if code == 0 else 4
    if fstype == "btrfs":
        # btrfs check: 0 = sauber, 1 = Fehler gefunden; alles andere = nicht prüfbar.
        if code == 0:
            return 0
        return 4 if code == 1 else 8
    if fstype in NTFS_TYPES:
        # ntfsfix -n: 0 = Basisprüfung bestanden, 1 = Korruption/Dirty-Flag; Befunde im Text siehe run_checker.
        if code == 0:
            return 0
        return 4 if code == 1 else 8
    if fstype in VMFS_TYPES:
        return 0 if code == 0 else 8
    return code


def ntfs_findings(output: str) -> bool:
    """True, wenn ntfsfix -n Korruption oder ein gesetztes Dirty-Flag meldet."""
    return any(_NTFS_FINDING.search(line) for line in output.splitlines())


def vfat_only_boot_label_mismatch(output: str) -> bool:
    """True, wenn fsck.fat nur das fehlende Wurzelverzeichnis-Label bemängelt (ESXi-Bootbank)."""
    lines = output.splitlines()
    return any(_VFAT_LABEL.match(line) for line in lines) and all(_VFAT_BENIGN.match(line) for line in lines)


def vmfs_findings(output: str) -> dict[str, int] | None:
    """Zählerstände aus der Zusammenfassung von fsck.vmfs; None, wenn sie fehlt."""
    found = {m[1]: int(m[2]) for line in output.splitlines() if (m := _VMFS_SUMMARY.match(line.strip()))}
    return found if len(found) == 5 else None


def _run_vmfs_check(device: str, env: dict) -> int:
    def run(tool: str) -> tuple[int, str]:
        command = [_tool(tool), device]
        print("$ " + " ".join(command), flush=True)
        result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                errors="replace", env=env)
        return result.returncode, result.stdout + result.stderr

    available = [t for t in ("fsck.vmfs6", "fsck.vmfs") if os.path.exists(_tool(t))]
    if not available:
        print("fsck.vmfs6/fsck.vmfs fehlen / missing. Installation: sudo apt install vmfs6-tools vmfs-tools", flush=True)
        return 8
    code, output = run(available[0])
    if "Unsupported version" in output and len(available) > 1:
        print(output.strip(), flush=True)
        print("Kein VMFS6 – Prüfung mit fsck.vmfs (VMFS3/5) / Not VMFS6 – checking with fsck.vmfs (VMFS3/5).", flush=True)
        code, output = run(available[1])
    detail = 0
    for line in output.splitlines():
        # Verlorene/doppelte Blöcke werden einzeln gemeldet, bei großen Datenspeichern sehr viele.
        if re.search(r"\bBlock 0x[0-9a-f]+ is\b", line):
            detail += 1
            if detail > _VMFS_MAX_DETAIL_LINES:
                continue
        print(line, flush=True)
    if detail > _VMFS_MAX_DETAIL_LINES:
        print(f"… {detail - _VMFS_MAX_DETAIL_LINES} weitere Blockmeldungen ausgelassen / further block messages omitted.", flush=True)
    findings = vmfs_findings(output)
    if code != 0 or findings is None:
        print("VMFS konnte nicht geöffnet bzw. nicht vollständig geprüft werden (z. B. VMFS-L/OSData, "
              "mehrere Extents, Versionswechsel) / VMFS could not be opened or fully checked.", flush=True)
        return 8
    print("Nur Metadaten geprüft (Inodes, Blockzuordnung, Verzeichnisse), keine Datenblöcke. Reparatur nur mit "
          "VMware VOMA unter ESXi / Metadata only, no data blocks. Repair only with VMware VOMA on ESXi.", flush=True)
    return 4 if any(findings.values()) else 0


def run_checker(action: str, device: str, fstype: str = "ext4") -> int:
    env = {**os.environ, "LC_ALL": "C"}
    command = checker_command(action, device, fstype)
    if fstype in VMFS_TYPES:
        return _run_vmfs_check(device, env)
    if not os.path.exists(command[0]):
        package = next(p for n, (a, b, p) in _TOOLS.items() if command[0] in (a, b))
        print(f"{command[0]} fehlt / missing. Installation: sudo apt install {package}", flush=True)
        return 8
    print("$ " + " ".join(command), flush=True)
    declined_fix = False
    skipped_journal = False
    dirty_log = False
    # unsquashfs -lls listet jede Datei; nur eine Zusammenfassung ausgeben.
    listing = fstype == "squashfs" and "-lls" in command
    listed = 0
    collected = []
    with subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True, errors="replace", env=env) as process:
        for line in process.stdout:
            if listing and line.startswith(("-", "d", "l", "c", "b", "p", "s")) and "/" in line:
                listed += 1
                continue
            print(line, end="", flush=True)
            collected.append(line)
            if fstype in {"ext2", "ext3", "ext4"}:
                declined_fix |= bool(re.search(r"\?\s*no\b", line))
                skipped_journal |= "skipping journal recovery" in line.lower()
            dirty_log |= "log" in line.lower() and "destroy" in line.lower()
        code = process.wait()
    if listing:
        print(f"{listed} Einträge gelesen / entries read (Metadaten; Datenblöcke nicht geprüft / data blocks not verified).",
              flush=True)
    if fstype == "xfs" and (code == 2 or dirty_log):
        print("XFS-Journal nicht leer: Dateisystem einmal einhängen und sauber aushängen, dann erneut prüfen. "
              "Kein xfs_repair -L (Datenverlust). / XFS log dirty: mount and cleanly unmount once, then check again.",
              flush=True)
    if fstype == "vfat" and action == "check" and code == 1 and vfat_only_boot_label_mismatch("".join(collected)):
        print("Hinweis: Label nur im Bootsektor (typisch für ESXi-Bootbanks BOOTBANK1/2) – kein Fehler. / "
              "Note: label in boot sector only (typical for ESXi boot banks) – not an error.", flush=True)
        return 0
    if fstype in NTFS_TYPES:
        print("Hinweis: ntfsfix -n prüft nur Bootsektor, MFT/MFT-Spiegel und Dirty-Flag. Eine vollständige "
              "Strukturprüfung ist nur unter Windows mit chkdsk /f möglich. / Note: ntfsfix -n checks only the boot "
              "sector, MFT/MFT mirror and dirty flag. A full structure check requires Windows chkdsk /f.", flush=True)
        if code == 0 and ntfs_findings("".join(collected)):
            return 4
    if fstype not in {"ext2", "ext3", "ext4"}:
        return normalize_exit(fstype, action, code)
    # Some e2fsck versions return 0 for a declined free-block-count correction.
    # Do not claim a clean filesystem merely from the exit status in that case.
    if action == "check" and skipped_journal:
        return 8
    if code == 0 and declined_fix:
        return 4
    return code if code >= 0 else 8


def worker_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("status", "check"))
    parser.add_argument("--language", choices=("de", "en"), default="de")
    for name in ("device", "uuid", "number", "fstype"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args(argv)
    try:
        if os.geteuid() != 0:
            raise PermissionError("Administrator authentication required / Administratorrechte erforderlich.")
        device = FilesystemDevice(args.device, args.fstype, args.uuid, args.number, (), "")
        if args.action == "status":
            current = validate_identity(device)
            report, code = read_online_status(current, args.language)
            print(report, flush=True)
            return code
        validate_device(device, args.action)
        # No implicit prompts or automatic unmounting. e2fsck also checks mounts.
        return run_checker(args.action, args.device, args.fstype)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr, flush=True)
        return 8
