from types import SimpleNamespace

from hardwaretest.ui import report_browser as browser


def test_chatgpt_association_cannot_select_chatgpt(monkeypatch):
    monkeypatch.setattr(browser.subprocess, "run", lambda *a, **kw: SimpleNamespace(stdout="chatgpt.desktop\n"))
    assert browser.browser_candidates()[0] == "firefox"
    assert "chatgpt" not in browser.browser_candidates()


def test_installed_default_browser_is_preferred(monkeypatch):
    monkeypatch.setattr(browser.subprocess, "run", lambda *a, **kw: SimpleNamespace(stdout="google-chrome.desktop\n"))
    assert browser.browser_candidates()[0] == "google-chrome"


def test_browser_fallback_and_file_url_encoding(monkeypatch, tmp_path):
    report = tmp_path / "report #1.html"
    report.write_text("<h1>test</h1>")
    calls = []
    monkeypatch.setattr(browser, "browser_candidates", lambda: ("firefox", "chromium"))
    monkeypatch.setattr(browser.shutil, "which", lambda name: "/usr/bin/" + name)
    def start(executable, url, directory):
        calls.append((executable, url, directory))
        return executable.endswith("chromium")
    monkeypatch.setattr(browser, "start_browser", start)
    assert browser.open_html_report(report)
    assert len(calls) == 2
    assert "file:///" in calls[0][1]
    assert "%23" in calls[0][1]


def test_no_browser_does_not_use_desktop_file_association(monkeypatch, tmp_path):
    report = tmp_path / "report.html"
    report.write_text("test")
    warnings = []
    monkeypatch.setattr(browser, "browser_candidates", lambda: ("firefox",))
    monkeypatch.setattr(browser.shutil, "which", lambda _: None)
    monkeypatch.setattr(browser.QMessageBox, "warning", lambda *args: warnings.append(args))
    assert not browser.open_html_report(report)
    assert warnings


def test_pyinstaller_libraries_not_passed_to_host_browser(monkeypatch):
    monkeypatch.setenv("LD_LIBRARY_PATH", "/opt/hardwaretest/_internal")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/system/lib")
    monkeypatch.setenv("QT_PLUGIN_PATH", "/opt/hardwaretest/_internal/plugins")
    env = browser.browser_environment()
    assert env["LD_LIBRARY_PATH"] == "/system/lib"
    assert "QT_PLUGIN_PATH" not in env
