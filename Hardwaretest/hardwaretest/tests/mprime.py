"""Adapter für mprime/Prime95 Torture Tests."""

from __future__ import annotations

from pathlib import Path
import shutil
from typing import Dict, List, Optional

from hardwaretest.core.test_runner import BaseTestRunner, TestParameters


def _default_mprime_path() -> str:
    """Suche mprime in dieser Reihenfolge:

    1. ``<project>/vendor/prime95/mprime``  (mitgeliefert)
    2. ``~/Prime95/mprime``                 (Legacy-Pfad)
    3. ``mprime`` im System-PATH
    """
    # 1. Im Projektverzeichnis (vendor/prime95/)
    project_root = Path(__file__).resolve().parent.parent.parent
    bundled = project_root / "vendor" / "prime95" / "mprime"
    if bundled.is_file():
        return str(bundled)

    # 2. Legacy: ~/Prime95/mprime
    legacy = Path.home() / "Prime95" / "mprime"
    if legacy.is_file():
        return str(legacy)

    # 3. Fallback: im PATH suchen
    found = shutil.which("mprime")
    if found:
        return found

    # Kein mprime gefunden – gib den gebündelten Pfad zurück (UI zeigt Fehler)
    return str(bundled)


_MODE_CODES = {
    "small_ffts": 0,
    "inplace_large_ffts": 1,
    "blend": 2,
    "custom": 3,
}


class MprimeRunner(BaseTestRunner):
    def __init__(
        self,
        params: TestParameters,
        mode: str = "blend",
        worker_threads: Optional[int] = None,
        custom_memory_mb: Optional[int] = None,
        fft_minutes: int = 15,
        binary_path: Optional[str] = None,
        use_config: bool = True,
        **kwargs,
    ) -> None:
        self.binary_path = str(Path(binary_path or _default_mprime_path()).expanduser())
        work_dir = Path(self.binary_path).parent
        super().__init__(params, work_dir=work_dir, **kwargs)
        self.mode = mode if mode in _MODE_CODES else "blend"
        self.worker_threads = worker_threads
        self.custom_memory_mb = custom_memory_mb
        self.fft_minutes = max(1, fft_minutes)
        self.use_config = use_config

    def build_command(self) -> List[str]:
        # mprime laeuft im Torture-Test-Modus (-t) und liest Einstellungen aus prime.txt
        return [self.binary_path, "-t"]

    def start(self) -> None:
        if self.use_config:
            self._prepare_local_config()
        super().start()

    def _prepare_local_config(self) -> None:
        work_dir = Path(self.binary_path).parent
        prime_txt = work_dir / "prime.txt"
        local_txt = work_dir / "local.txt"

        # prime.txt ist die primaere Konfigurationsdatei (v30.11+).
        # Bestehende Eintraege beibehalten, nur Torture-Settings aktualisieren.
        config = self._load_config(prime_txt)

        # Pflicht-Keys fuer einen sauberen Headless-Torture-Test
        config.setdefault("StressTester", "1")
        if config.get("StressTester") == "99":
            config["StressTester"] = "1"
        config.setdefault("UsePrimenet", "0")

        # Torture-Test-Einstellungen
        mode_code = _MODE_CODES.get(self.mode, _MODE_CODES["blend"])
        config["TortureTest"] = str(mode_code)
        if self.custom_memory_mb:
            config["TortureMem"] = str(self.custom_memory_mb)
        if self.worker_threads:
            config["TortureThreads"] = str(self.worker_threads)
        config["TortureTime"] = str(self.fft_minutes)

        self._write_config(prime_txt, config)

        # Rueckwaertskompatibilitaet: aeltere mprime-Versionen lesen local.txt
        legacy = self._load_config(local_txt)
        legacy["TortureTest"] = str(mode_code)
        if self.custom_memory_mb:
            legacy["TortureMem"] = str(self.custom_memory_mb)
        if self.worker_threads:
            legacy["TortureThreads"] = str(self.worker_threads)
        legacy["TortureTime"] = str(self.fft_minutes)
        self._write_config(local_txt, legacy)

    @staticmethod
    def _load_config(cfg_path: Path) -> Dict[str, str]:
        """Liest eine mprime-Konfigurationsdatei (prime.txt / local.txt).

        Gibt ein OrderedDict-artiges Dict zurueck. Sektions-Header wie
        ``[Internals]`` werden unter dem Sonderschluessel
        ``__section__<name>`` gespeichert, damit sie beim Zurueckschreiben
        an der richtigen Stelle erhalten bleiben.
        """
        if not cfg_path.exists():
            sample = cfg_path.with_suffix(".bak")
            if sample.exists():
                shutil.copy(sample, cfg_path)
            return {}
        backup = cfg_path.with_suffix(".hwbackup")
        if cfg_path.exists() and not backup.exists():
            shutil.copy(cfg_path, backup)
        entries: Dict[str, str] = {}
        for line in cfg_path.read_text().splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            # Sektions-Header beibehalten
            if stripped.startswith("[") and stripped.endswith("]"):
                section_name = stripped[1:-1]
                entries[f"__section__{section_name}"] = stripped
                continue
            if "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            entries[key.strip()] = value.strip()
        return entries

    @staticmethod
    def _write_config(cfg_path: Path, data: Dict[str, str]) -> None:
        """Schreibt eine mprime-Konfigurationsdatei.

        Sektions-Header (``__section__*`` Keys) werden als ``[Section]``
        geschrieben, normale Keys als ``Key=Value``.
        """
        lines: list[str] = []
        for key, value in data.items():
            if key.startswith("__section__"):
                lines.append("")  # Leerzeile vor Sektion
                lines.append(value)
            else:
                lines.append(f"{key}={value}")
        cfg_path.write_text("\n".join(lines) + "\n")
