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
        return self.fstype in {"ext2", "ext3", "ext4"}


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


def validate_device(expected: FilesystemDevice) -> FilesystemDevice:
    """Fail closed on identity changes, mounts, active layers or unreadable state."""
    current = validate_identity(expected)
    if not current.supported or current.kind not in {"part", "disk", "lvm", "crypt", "raid0", "raid1", "raid4", "raid5", "raid6", "raid10", "md"}:
        raise ValueError("Only offline ext2/ext3/ext4 / Nur ext2/ext3/ext4 offline unterstützt.")
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


def checker_command(action: str, device: str) -> list[str]:
    if action not in {"check", "repair"}:
        raise ValueError("Unknown filesystem action")
    # -n is strictly read-only. -p repairs only problems e2fsck considers safe;
    # never use -y, never run the generic fsck dispatcher on mounted filesystems.
    return ["/usr/sbin/e2fsck", "-f", "-n" if action == "check" else "-p", device]


def worker_command(action: str, device: FilesystemDevice, language: str = "de") -> list[str]:
    program = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, "-m", "hardwaretest"]
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
    if code in (1, 2, 3) and action == "repair":
        message = t("Fehler korrigiert. Erneute Prüfung empfohlen.", "Errors corrected. A new check is recommended.")
        if code & 2:
            message += t(" Neustart erforderlich.", " Reboot required.")
        return message
    if code >= 0 and code < 8 and code & 4:
        return t("Dateisystemfehler vorhanden; nicht alle korrigiert. Siehe Protokoll.",
                 "Filesystem errors remain; not all corrected. See log.")
    return t("Prüfung/Reparatur nicht erfolgreich abgeschlossen – keine Zustandsaussage.",
             "Check/repair did not complete successfully – health unknown.") + f" (Exit: {code})"


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
        if device.fstype in {"ntfs", "ntfs3"}:
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


def run_checker(action: str, device: str) -> int:
    env = {**os.environ, "LC_ALL": "C"}
    command = checker_command(action, device)
    print("$ " + " ".join(command), flush=True)
    declined_fix = False
    skipped_journal = False
    with subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True, errors="replace", env=env) as process:
        for line in process.stdout:
            print(line, end="", flush=True)
            declined_fix |= bool(re.search(r"\?\s*no\b", line))
            skipped_journal |= "skipping journal recovery" in line.lower()
        code = process.wait()
    # Some e2fsck versions return 0 for a declined free-block-count correction.
    # Do not claim a clean filesystem merely from the exit status in that case.
    if action == "check" and skipped_journal:
        return 8
    if code == 0 and declined_fix:
        return 4
    return code if code >= 0 else 8


def worker_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("status", "check", "repair"))
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
        validate_device(device)
        # No implicit prompts or automatic unmounting. e2fsck also checks mounts.
        return run_checker(args.action, args.device)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr, flush=True)
        return 8
