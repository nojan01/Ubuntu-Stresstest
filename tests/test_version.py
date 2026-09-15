from pathlib import Path

from hardwaretest import __version__


def test_package_version_matches_pyproject():
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    expected = next(
        line.split('"', 2)[1]
        for line in pyproject.read_text(encoding="utf-8").splitlines()
        if line.startswith("version = ")
    )
    assert __version__ == expected
