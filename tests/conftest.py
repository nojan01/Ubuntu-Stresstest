import pytest

from hardwaretest.core import test_plan


@pytest.fixture(scope="session", autouse=True)
def _isolated_qsettings(tmp_path_factory):
    """Tests dürfen die QSettings des Benutzers weder lesen noch verändern.

    Die Sprache wird fest auf Deutsch gesetzt, da die Tests deutsche Texte
    erwarten.
    """
    from PySide6.QtCore import QSettings
    QSettings.setPath(QSettings.NativeFormat, QSettings.UserScope,
                      str(tmp_path_factory.mktemp("qsettings")))
    from hardwaretest.ui.i18n import language_manager
    language_manager.set_language("de")
    yield


@pytest.fixture(autouse=True)
def _no_real_pkexec(monkeypatch):
    """Unit-Tests dürfen nie eine echte pkexec-Abfrage auslösen."""
    monkeypatch.setattr(test_plan.TestPlanExecutor, "_needs_root", lambda self: False)


@pytest.fixture(autouse=True)
def _no_real_rasdaemon(monkeypatch):
    """Runner-Tests dürfen nicht die rasdaemon-Datenbank des Rechners lesen."""
    from hardwaretest.core import ras
    monkeypatch.setattr(ras, "rasdaemon_installed", lambda: False)
