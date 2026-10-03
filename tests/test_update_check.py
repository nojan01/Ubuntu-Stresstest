from hardwaretest.core.update_check import is_newer, parse_release, parse_version, update_check_disabled

PAYLOAD = {
	"tag_name": "v0.2.31",
	"html_url": "https://github.com/nojan01/Ubuntu-Stresstest/releases/tag/v0.2.31",
	"assets": [
		{"name": "hardwaretest_0.2.31_amd64.deb", "browser_download_url": "https://x/deb"},
		{"name": "Hardwaretest-0.2.31-x86_64.AppImage", "browser_download_url": "https://x/appimage"},
	],
}


class Test_UpdateCheck:
	def test_parse_version(self):
		assert parse_version("v0.2.30") == (0, 2, 30)
		assert parse_version("kein") is None

	def test_is_newer(self):
		assert is_newer("0.2.31", "0.2.30")
		assert is_newer("0.10.0", "0.9.9")
		assert not is_newer("0.2.30", "0.2.30")
		assert not is_newer("0.2", "0.2.0")
		assert not is_newer("0.2.29", "0.2.30")

	def test_parse_release_picks_asset(self):
		assert parse_release(PAYLOAD, ".AppImage").download_url == "https://x/appimage"
		release = parse_release(PAYLOAD, ".deb")
		assert release.version == "0.2.31"
		assert release.download_url == "https://x/deb"

	def test_parse_release_falls_back_and_skips_prerelease(self):
		assert parse_release({**PAYLOAD, "assets": []}, ".deb").download_url == PAYLOAD["html_url"]
		assert parse_release({**PAYLOAD, "prerelease": True}) is None

	def test_disable_env(self, monkeypatch):
		monkeypatch.setenv("HARDWARETEST_NO_UPDATE_CHECK", "1")
		assert update_check_disabled()
		monkeypatch.delenv("HARDWARETEST_NO_UPDATE_CHECK")
		assert not update_check_disabled()
