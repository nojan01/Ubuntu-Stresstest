"""Alle literalen ``language_manager.tr("…")``-Schlüssel in hardwaretest/ui/ müssen übersetzt sein.

Ein Schlüssel gilt als übersetzt, wenn er direkt in ``_EN`` steht oder bewusst in
``PARTS_ONLY`` eingetragen ist (Texte, die ``LanguageManager.tr`` vollständig über die
Teilersetzungen in ``_EN_PARTS`` abdeckt). Platzhalter-Mengen von Schlüssel und
Übersetzung müssen übereinstimmen.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from hardwaretest.ui import i18n

UI_DIR = Path(i18n.__file__).resolve().parent
_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)[^}]*\}")

# Absichtlich nur über _EN_PARTS übersetzt; jeder neue Eintrag hier muss durch die
# Teilersetzungen tatsächlich vollständig englisch werden (siehe Test unten).
PARTS_ONLY: dict[str, str] = {
    "Heuristischer Fortschritt: {progress}%{suffix}": "Estimated Progress: {progress}%{suffix}",
    "Fortschritt (Write+Verify): {progress}%": "Progress (Write+Verify): {progress}%",
}


def _is_tr_call(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr == "tr":
        return isinstance(func.value, (ast.Name, ast.Attribute))
    return isinstance(func, ast.Name) and func.id == "tr"


def collect_tr_keys(root: Path = UI_DIR) -> dict[str, list[str]]:
    keys: dict[str, list[str]] = {}
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not _is_tr_call(node) or not node.args:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                location = f"{path.relative_to(root.parent.parent)}:{node.lineno}"
                keys.setdefault(first.value, []).append(location)
    return keys


def _apply_parts(key: str) -> str:
    text = key
    for source, translated in i18n._EN_PARTS:
        text = text.replace(source, translated)
    return text


def _placeholders(text: str) -> set[str]:
    return set(_PLACEHOLDER.findall(text))


def test_all_literal_tr_keys_have_english_translation():
    missing = []
    for key, locations in collect_tr_keys().items():
        if key in i18n._EN or key in PARTS_ONLY:
            continue
        missing.append(f"{key!r} ({', '.join(locations)})")
    assert not missing, "Fehlende englische Übersetzungen:\n" + "\n".join(missing)


def test_parts_only_keys_translate_completely():
    for key, expected in PARTS_ONLY.items():
        assert key not in i18n._EN, f"{key!r} steht in _EN und braucht keinen PARTS_ONLY-Eintrag"
        assert _apply_parts(key) == expected


def test_translations_keep_placeholders():
    mismatched = [
        f"{key!r} -> {value!r}"
        for key, value in i18n._EN.items()
        if _placeholders(key) != _placeholders(value)
    ]
    assert not mismatched, "Platzhalter weichen ab:\n" + "\n".join(mismatched)


@pytest.mark.parametrize("key", ["Tests laufen", "Aktion läuft"])
def test_sample_keys_translate(key):
    assert key in i18n._EN
