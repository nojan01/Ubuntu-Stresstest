"""Prüfung auf neue Hardwaretest-Versionen über GitHub-Releases."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
from typing import Optional, Tuple
import urllib.request

RELEASES_API = "https://api.github.com/repos/nojan01/Ubuntu-Stresstest/releases/latest"
DISABLE_ENV = "HARDWARETEST_NO_UPDATE_CHECK"


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    page_url: str
    download_url: str


def parse_version(text: str) -> Optional[Tuple[int, ...]]:
    match = re.search(r"(\d+(?:\.\d+)*)", text or "")
    if not match:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def is_newer(candidate: str, current: str) -> bool:
    new, old = parse_version(candidate), parse_version(current)
    if new is None or old is None:
        return False
    width = max(len(new), len(old))
    return new + (0,) * (width - len(new)) > old + (0,) * (width - len(old))


def update_check_disabled() -> bool:
    return os.environ.get(DISABLE_ENV, "").strip().lower() in {"1", "true", "yes", "ja"}


def _preferred_asset_suffix() -> str:
    # AppImage-Laufzeit setzt $APPIMAGE; sonst ist die .deb-Installation wahrscheinlicher.
    return ".AppImage" if os.environ.get("APPIMAGE") else ".deb"


def parse_release(payload: dict, asset_suffix: Optional[str] = None) -> Optional[ReleaseInfo]:
    tag = str(payload.get("tag_name") or "")
    version_tuple = parse_version(tag)
    if version_tuple is None or payload.get("draft") or payload.get("prerelease"):
        return None
    page_url = str(payload.get("html_url") or "")
    suffix = asset_suffix or _preferred_asset_suffix()
    download_url = page_url
    for asset in payload.get("assets") or []:
        name = str(asset.get("name") or "")
        if name.endswith(suffix):
            download_url = str(asset.get("browser_download_url") or page_url)
            break
    return ReleaseInfo(
        version=".".join(str(part) for part in version_tuple),
        page_url=page_url,
        download_url=download_url,
    )


def fetch_latest_release(timeout: float = 5.0) -> Optional[ReleaseInfo]:
    """Liefert das neueste Release oder wirft OSError/ValueError bei Netzfehlern."""
    request = urllib.request.Request(
        RELEASES_API,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "Hardwaretest-Update-Check"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Unerwartete Antwort der GitHub-API")
    return parse_release(payload)
