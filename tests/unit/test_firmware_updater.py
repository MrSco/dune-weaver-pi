"""DW Mini fork: firmware version ordering and the update source switch."""
from modules.update import firmware_updater as fu


def test_parse_upstream_and_mini_versions():
    assert fu.parse_version("v0.1.10 (main-5ce4400d-dirty)") == (0, 1, 10, 0)
    assert fu.parse_version("v0.2.6 (mini-unipolar-6d583fa0-dirty)") == (0, 2, 6, 0)
    assert fu.parse_version("v0.2.6-mini.3") == (0, 2, 6, 3)
    assert fu.parse_version("v0.2.6-mini.12 (mini-unipolar-abc)") == (0, 2, 6, 12)
    assert fu.parse_version("garbage") is None


def test_mini_releases_order_after_their_base_and_each_other():
    assert fu.is_newer("v0.2.6-mini.1", "v0.2.6 (mini-unipolar-6d583fa0-dirty)")
    assert fu.is_newer("v0.2.6-mini.2", "v0.2.6-mini.1")
    assert not fu.is_newer("v0.2.6-mini.1", "v0.2.6-mini.1")
    assert not fu.is_newer("v0.2.6-mini.1", "v0.2.6-mini.2")
    assert fu.is_newer("v0.2.7-mini.1", "v0.2.6-mini.9")


def test_no_release_lookup_without_a_configured_repo(monkeypatch):
    monkeypatch.setattr(fu, "FIRMWARE_REPO", "")

    def boom(*a, **k):
        raise AssertionError("must not query GitHub")

    monkeypatch.setattr(fu.requests, "get", boom)
    assert fu.get_latest_release(force=True) is None
