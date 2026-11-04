"""Optional NVIDIA DCGM diagnostic runner."""

from __future__ import annotations

import json
import shutil
from typing import Optional, Sequence

from hardwaretest.core import privilege
from hardwaretest.core.test_runner import BaseTestRunner, TestExecutionError, TestParameters


def dcgmi_available() -> bool:
    return shutil.which("dcgmi") is not None


class DcgmDiagnosticRunner(BaseTestRunner):
    def __init__(
        self,
        params: TestParameters,
        gpu_indices: Sequence[int],
        run_level: int,
        log_fn=None,
        use_pkexec: bool = False,
        language: str = "de",
        skip_p2p: bool = False,
    ) -> None:
        if not gpu_indices:
            raise TestExecutionError("Keine NVIDIA-GPU ausgewählt")
        if run_level not in {1, 2, 3, 4}:
            raise TestExecutionError("Ungültige DCGM-Diagnosestufe")
        super().__init__(params, log_fn=log_fn)
        self.gpu_indices = sorted(set(int(index) for index in gpu_indices))
        self.run_level = run_level
        self.language = language
        # PCIe-Peer-to-Peer-Test ueberspringen: bei aktivem IOMMU/ACS oder GPUs an
        # verschiedenen Sockeln schlaegt er mit "buffer mismatch" fehl (kein Defekt).
        self.skip_p2p = skip_p2p
        self.output_lines: list[str] = []
        self._command_prefix: list[str] = []
        if use_pkexec and not privilege.is_root():
            if privilege.tool() is None:
                raise TestExecutionError(privilege.missing_text(language))
            self._command_prefix = privilege.command_prefix("DCGM-Diagnose")

    def build_command(self) -> list[str]:
        if not dcgmi_available():
            raise TestExecutionError("dcgmi ist nicht installiert")
        entities = ",".join(f"gpu:{index}" for index in self.gpu_indices)
        return [
            *self._command_prefix,
            "dcgmi",
            "diag",
            "--run",
            str(self.run_level),
            "--entity-id",
            entities,
            # pcie laeuft erst ab Stufe 2; Parametername laut DCGM-Handbuch "test_p2p_on"
            *(["--parameters", "pcie.test_p2p_on=false"] if self.skip_p2p and self.run_level >= 2 else []),
            "--json",
        ]

    def _stream_output(self) -> None:
        if not self._process or not self._process.stdout:
            return
        for line in self._process.stdout:
            stripped = line.rstrip()
            self.output_lines.append(stripped)
            # dcgmi --json liefert das Ergebnis erst am Ende als Block; Rohzeilen
            # wuerden das Protokoll nur mit JSON fluten -> lesbarer Bericht unten.
            if not stripped.lstrip().startswith(("{", "}", "[", "]", '"')):
                self._log(stripped)
            self._check_line_for_errors(stripped)
        self._process.wait()
        for text in self.report_lines(self.language):
            self._log(text)
        self._finalize_result()

    def parsed_payload(self) -> Optional[object]:
        """Das aus der dcgmi-Ausgabe extrahierte JSON oder None, wenn keins vorliegt."""
        return _extract_json("\n".join(self.output_lines))

    def evaluation_error(self) -> Optional[str]:
        """Begruendung, warum das Ergebnis nicht auswertbar ist (None = auswertbar).

        Erfolg setzt gueltiges JSON und mindestens einen erkannten Teststatus voraus;
        sonst wuerden „0 bestanden, 0 fehlgeschlagen“ faelschlich als bestanden gelten.
        """
        payload = self.parsed_payload()
        if payload is None:
            return "dcgmi lieferte kein auswertbares JSON"
        runtime_error = _lookup(payload, ("runtime_error",))
        if isinstance(runtime_error, str) and runtime_error.strip():
            return "DCGM: " + " ".join(runtime_error.split())
        counts = self.status_counts()
        if sum(counts.values()) == 0:
            return "dcgmi-JSON enthält keinen Teststatus"
        return None

    def status_counts(self) -> dict[str, int]:
        """Count DCGM result states without relying on one schema version."""
        payload = self.parsed_payload()
        counts = {"pass": 0, "fail": 0, "warn": 0, "skip": 0}

        def visit(value: object) -> None:
            if isinstance(value, dict):
                status = value.get("status")
                if isinstance(status, str):
                    normalized = status.casefold()
                    for key in counts:
                        if normalized.startswith(key):
                            counts[key] += 1
                            break
                for nested in value.values():
                    visit(nested)
            elif isinstance(value, list):
                for nested in value:
                    visit(nested)

        if payload is not None:
            visit(payload)
        return counts

    def report_lines(self, language: Optional[str] = None) -> list[str]:
        """Lesbare Textfassung des dcgmi-JSON (Kategorie -> Test -> GPU-Status).

        Faellt auf die Rohausgabe zurueck, wenn kein JSON vorliegt.
        """
        payload = self.parsed_payload()
        if payload is None:
            return list(self.output_lines)
        return format_dcgm_report(payload, language=language or self.language)


_STATUS_SYMBOLS = {"pass": "✓", "fail": "✗", "warn": "!", "skip": "–"}
_STATUS_WORDS = {
    "de": {"pass": "bestanden", "fail": "FEHLGESCHLAGEN", "warn": "Warnung", "skip": "übersprungen"},
    "en": {"pass": "passed", "fail": "FAILED", "warn": "warning", "skip": "skipped"},
}


def _status_key(status: object) -> Optional[str]:
    if not isinstance(status, str):
        return None
    normalized = status.casefold()
    for key in _STATUS_SYMBOLS:
        if normalized.startswith(key):
            return key
    return None


def _messages(value: object) -> list[str]:
    """Normalisiert warnings/info/errors aus den verschiedenen DCGM-Schemaversionen."""
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [" ".join(value.split())]
    if isinstance(value, dict):
        for key in ("warning", "error", "info", "message", "msg"):
            if isinstance(value.get(key), str):
                text = " ".join(value[key].split())
                if value.get("error_id") not in (None, 0, "0", ""):
                    text += f" (ID {value['error_id']})"
                return [text]
        return [json.dumps(value, ensure_ascii=False)]
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            out.extend(_messages(item))
        return out
    return [str(value)]


def _find_categories(value: object) -> tuple[Optional[dict], list]:
    """Sucht rekursiv den Block mit ``test_categories`` (DCGM 2.x–4.x) bzw. eine Testliste."""
    if isinstance(value, dict):
        categories = value.get("test_categories")
        if isinstance(categories, list):
            return value, categories
        tests = value.get("tests")
        if isinstance(tests, list) and tests and all(isinstance(t, dict) and "results" in t for t in tests):
            return value, [{"category": value.get("category", ""), "tests": tests}]
        for nested in value.values():
            root, found = _find_categories(nested)
            if found:
                return root, found
    elif isinstance(value, list):
        for nested in value:
            root, found = _find_categories(nested)
            if found:
                return root, found
    return None, []


def _lookup(payload: object, keys: Sequence[str]) -> object:
    """Erster Treffer eines Schluessels im Kopfbereich (nicht innerhalb der Testergebnisse)."""
    if isinstance(payload, dict):
        for key in keys:
            if payload.get(key) not in (None, "", [], {}):
                return payload[key]
        for name, nested in payload.items():
            if name == "test_categories":
                continue
            found = _lookup(nested, keys)
            if found is not None:
                return found
    elif isinstance(payload, list):
        for nested in payload:
            found = _lookup(nested, keys)
            if found is not None:
                return found
    return None


def _entity_label(result: dict) -> str:
    gpu = result.get("gpu_id", result.get("gpu_ids"))
    if gpu not in (None, ""):
        return f"GPU {gpu}"
    entity = result.get("entity_id")
    if entity in (None, ""):
        entity = (result.get("entity") or {}).get("entity_id") if isinstance(result.get("entity"), dict) else None
    if entity in (None, ""):
        return ""
    group = str(result.get("entity_group") or "GPU")
    return f"{group} {entity}"


def format_dcgm_report(payload: object, language: str = "de") -> list[str]:
    words = _STATUS_WORDS.get(language, _STATUS_WORDS["de"])
    de = language != "en"
    _root, categories = _find_categories(payload)

    lines: list[str] = []
    label = "DCGM-Diagnose" if de else "DCGM diagnostic"
    version = _lookup(payload, ("version", "DCGM Version", "dcgm_version"))
    lines.append(f"{label}" + (f" (DCGM {version})" if version else ""))
    driver = _lookup(payload, ("Driver Version Detected", "driver_version", "Driver Version"))
    if driver:
        lines.append(("Treiber: " if de else "Driver: ") + str(driver))
    serials = _lookup(payload, ("GPU Device Serials", "gpu_serials"))
    if isinstance(serials, dict):
        pairs = ", ".join(f"GPU {gpu}: {sn or '?'}" for gpu, sn in serials.items())
        lines.append(("Seriennummern: " if de else "Serial numbers: ") + pairs)
    dev_ids = _lookup(payload, ("GPU Device IDs", "gpu_device_ids"))
    if isinstance(dev_ids, list):
        lines.append(("Geräte-IDs: " if de else "Device IDs: ") + ", ".join(str(d) for d in dev_ids))

    for category in categories:
        if not isinstance(category, dict):
            continue
        lines.append("")
        name_cat = category.get("category") or category.get("name") or "?"
        lines.append(f"[{name_cat}]")
        for test in category.get("tests") or []:
            if not isinstance(test, dict):
                continue
            name = str(test.get("name", "?"))
            results = test.get("results")
            if results in (None, "", []):
                summary = test.get("test_summary") if isinstance(test.get("test_summary"), dict) else {}
                results = [{"status": summary.get("status", test.get("status", "?"))}]
            if not isinstance(results, list):
                results = [results]
            result_keys = set()
            for result in results:
                if not isinstance(result, dict):
                    continue
                key = _status_key(result.get("status"))
                result_keys.add(key)
                symbol = _STATUS_SYMBOLS.get(key, "?") if key else "?"
                status_text = words[key] if key else str(result.get("status", "?"))
                target = _entity_label(result)
                lines.append(f"  {symbol} {name}{' – ' + target if target else ''}: {status_text}")
                for text in _messages(result.get("warnings")):
                    lines.append(("      Warnung: " if de else "      Warning: ") + text)
                for text in _messages(result.get("errors")):
                    lines.append(("      Fehler: " if de else "      Error: ") + text)
                for text in _messages(result.get("info")):
                    lines.append("      Info: " + text)
            # DCGM 4: Gesamtstatus des Tests kann schlechter als die GPU-Einzelergebnisse
            # sein (Fail im test_summary bei bestandenen GPUs) -> sichtbar machen.
            summary = test.get("test_summary") if isinstance(test.get("test_summary"), dict) else None
            if summary and test.get("results") not in (None, "", []):
                summary_key = _status_key(summary.get("status"))
                extra = [(k, _messages(summary.get(k))) for k in ("errors", "warnings")]
                rank = {"pass": 0, "skip": 1, "warn": 2, "fail": 3}
                worst = max((rank.get(k, 0) for k in result_keys), default=0)
                if summary_key and (rank[summary_key] > worst or any(texts for _k, texts in extra)):
                    symbol = _STATUS_SYMBOLS.get(summary_key, "?")
                    lines.append(f"  {symbol} {name} – {'Gesamt' if de else 'overall'}: {words[summary_key]}")
                    for k, texts in extra:
                        prefix = ("      Fehler: " if de else "      Error: ") if k == "errors" else (
                            "      Warnung: " if de else "      Warning: ")
                        lines.extend(prefix + t for t in texts)

    for key_name in ("errors", "warnings"):
        texts = _messages(_lookup(payload, (key_name,)))
        if texts:
            lines.append("")
            lines.append(("Fehler:" if key_name == "errors" else "Warnungen:") if de else key_name.capitalize() + ":")
            lines.extend("  " + t for t in texts)

    runtime_error = _lookup(payload, ("runtime_error",))
    if isinstance(runtime_error, str) and runtime_error.strip():
        lines.append("")
        lines.append(("DCGM-Fehler: " if de else "DCGM error: ") + " ".join(runtime_error.split()))
    if not categories and not runtime_error:
        lines.append("")
        lines.append("Unbekanntes Ergebnisformat – Rohdaten:" if de else "Unknown result format – raw data:")
        lines.append(json.dumps(payload, indent=2, ensure_ascii=False))
    return lines


def failed_checks(payload: object) -> list[str]:
    """Namen der Prüfungen mit Status Fail, z. B. ``["pcie (GPU 0)", "memory (Gesamt)"]``."""
    _root, categories = _find_categories(payload)
    names: list[str] = []
    for category in categories:
        if not isinstance(category, dict):
            continue
        for test in category.get("tests") or []:
            if not isinstance(test, dict):
                continue
            name = str(test.get("name", "?"))
            results = test.get("results")
            if isinstance(results, dict):
                results = [results]
            for result in results if isinstance(results, list) else []:
                if isinstance(result, dict) and _status_key(result.get("status")) == "fail":
                    target = _entity_label(result)
                    names.append(f"{name} ({target})" if target else name)
            summary = test.get("test_summary") if isinstance(test.get("test_summary"), dict) else None
            if (summary and _status_key(summary.get("status")) == "fail") or (
                    not results and _status_key(test.get("status")) == "fail"):
                if not any(n == name or n.startswith(name + " (") for n in names):
                    names.append(name)
    return names


def _extract_json(output: str) -> Optional[object]:
    start = output.find("{")
    end = output.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        return json.loads(output[start:end + 1])
    except json.JSONDecodeError:
        return None


def cuda_driver_available() -> bool:
    """True, wenn die CUDA-Treiberbibliothek (Teil des NVIDIA-Treibers) da ist."""
    import ctypes

    try:
        ctypes.CDLL("libcuda.so.1")
    except OSError:
        return False
    return True


class GpuStressRunner(BaseTestRunner):
    """GPU-Lasttest (Rechenlast + VRAM-Muster) über die CUDA-Treiber-API."""

    def __init__(
        self,
        params: TestParameters,
        gpu_indices: Sequence[int],
        memory_percent: int = 80,
        log_fn=None,
    ) -> None:
        if not gpu_indices:
            raise TestExecutionError("Keine NVIDIA-GPU ausgewählt")
        super().__init__(params, log_fn=log_fn)
        self.gpu_indices = sorted(set(int(index) for index in gpu_indices))
        self.memory_percent = memory_percent

    def build_command(self) -> list[str]:
        import sys
        from pathlib import Path

        if not cuda_driver_available():
            raise TestExecutionError(
                "libcuda.so.1 nicht gefunden – NVIDIA-Treiber mit CUDA-Unterstützung nötig"
            )
        duration = self.params.duration_seconds
        script_duration = duration - 5 if duration > 10 else duration
        if getattr(sys, "frozen", False):
            cmd = [sys.executable, "--gpu-stress-worker"]
        else:
            cmd = [sys.executable, str(Path(__file__).with_name("gpu_stress_script.py"))]
        return [*cmd, "--gpus", ",".join(str(index) for index in self.gpu_indices), "--duration", str(script_duration), "--memory-percent", str(self.memory_percent)]

    def _check_line_for_errors(self, line: str) -> None:
        if "FEHLER" in line:
            self._remember_error(line.strip())
            return
        super()._check_line_for_errors(line)
