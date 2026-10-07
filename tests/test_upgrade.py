import httpx
import pytest

from djlib.domain.errors import AppError
from djlib.interfaces import upgrade_cli


def test_versions_order_like_releases():
    key = upgrade_cli.version_key
    assert key("0.1.0a10") > key("0.1.0a9") > key("0.1.0a9.dev0") > key("0.1.0a3")
    assert key("0.1.0") > key("0.1.0rc1") > key("0.1.0b2") > key("0.1.0a11")
    assert key("0.2.0a1") > key("0.2.0.dev1") > key("0.1.9")
    assert key("0.1.0.post1") > key("0.1.0") and key("0.1.0a13") == key("0.1.0a13")


def test_latest_release_skips_drafts_and_finds_the_wheel(monkeypatch):
    releases = [
        {"draft": True, "assets": [{"name": "dj_library_tool-0.1.0a99-py3-none-any.whl"}]},
        {
            "draft": False,
            "tag_name": "v0.1.0a12",
            "html_url": "https://github.com/x/y/releases/tag/v0.1.0a12",
            "assets": [
                {"name": "SHA256SUMS", "browser_download_url": "https://e/s"},
                {
                    "name": "dj_library_tool-0.1.0a12-py3-none-any.whl",
                    "browser_download_url": "https://e/w.whl",
                },
            ],
        },
    ]

    class Reply:
        def raise_for_status(self):
            return None

        def json(self):
            return releases

    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: Reply())
    latest = upgrade_cli.latest_release()
    assert latest == {
        "version": "0.1.0a12",
        "tag": "v0.1.0a12",
        "url": "https://e/w.whl",
        "notes": "https://github.com/x/y/releases/tag/v0.1.0a12",
    }

    def offline(*args, **kwargs):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(httpx, "get", offline)
    with pytest.raises(AppError) as error:
        upgrade_cli.latest_release()
    assert error.value.code == "UPDATE_CHECK_FAILED" and error.value.retryable
