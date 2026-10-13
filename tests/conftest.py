import pytest

from hardwaretest.core import test_plan


@pytest.fixture(autouse=True)
def _no_real_pkexec(monkeypatch):
    """Unit-Tests dürfen nie eine echte pkexec-Abfrage auslösen."""
    monkeypatch.setattr(test_plan.TestPlanExecutor, "_needs_root", lambda self: False)
