from hardwaretest.core import distro

_os_release = distro.os_release


def _family_for(tmp_path, text, monkeypatch):
    path = tmp_path / "os-release"
    path.write_text(text, encoding="utf-8")
    info = _os_release(path)
    monkeypatch.setattr(distro, "os_release", lambda p=None: info)
    distro.family.cache_clear()
    try:
        return distro.family()
    finally:
        distro.family.cache_clear()


def test_os_release_parsing(tmp_path):
    path = tmp_path / "os-release"
    path.write_text('ID="almalinux"\nID_LIKE="rhel centos fedora"\n# comment\nNAME=AlmaLinux\n')
    info = distro.os_release(path)
    assert info["ID"] == "almalinux" and info["ID_LIKE"] == "rhel centos fedora" and info["NAME"] == "AlmaLinux"


def test_family_and_install_command(tmp_path, monkeypatch):
    assert _family_for(tmp_path, 'ID=almalinux\nID_LIKE="rhel centos fedora"\n', monkeypatch) == "rhel"
    assert _family_for(tmp_path, 'ID=ubuntu\nID_LIKE=debian\n', monkeypatch) == "debian"
    assert _family_for(tmp_path, 'ID=opensuse-leap\nID_LIKE="suse opensuse"\n', monkeypatch) == "suse"

    monkeypatch.setattr(distro, "family", lambda: "rhel")
    assert distro.install_command("zfsutils-linux", "ipmitool") == "sudo dnf install zfs ipmitool"
    monkeypatch.setattr(distro, "family", lambda: "debian")
    assert distro.install_command("memtest86+") == "sudo apt install memtest86+"
    monkeypatch.setattr(distro, "family", lambda: "unknown")
    assert distro.install_command("fio") == "sudo apt install fio"


def test_package_installed_uses_rpm_when_no_dpkg(monkeypatch):
    calls = []
    monkeypatch.setattr(distro.shutil, "which", lambda name: "/usr/bin/rpm" if name == "rpm" else None)

    class Result:
        returncode = 0
        stdout = ""

    def run(cmd, **kwargs):
        calls.append(cmd)
        return Result()
    monkeypatch.setattr(distro.subprocess, "run", run)
    monkeypatch.setattr(distro, "family", lambda: "rhel")
    assert distro.package_installed("zfsutils-linux") is True
    assert calls == [["rpm", "-q", "zfs"]]
