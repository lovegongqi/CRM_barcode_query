import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "install_packaged_chromium.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("install_packaged_chromium", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_parse_install_locations_reads_current_playwright_directories(tmp_path):
    module = _load_module()
    first = tmp_path / "chromium-1228"
    second = tmp_path / "ffmpeg-1011"
    output = f"""
Chrome for Testing
  Install location:    {first}

FFmpeg
  Install location:    {second}
"""

    assert module.parse_install_locations(output) == [first, second]


def test_copy_installations_only_copies_complete_current_revisions(tmp_path):
    module = _load_module()
    chromium = tmp_path / "cache" / "chromium-1228"
    chromium.mkdir(parents=True)
    (chromium / "INSTALLATION_COMPLETE").touch()
    (chromium / "chrome").write_text("browser", encoding="utf-8")
    target = tmp_path / "package" / "ms-playwright"

    copied = module.copy_installations([chromium], target)

    assert copied == [target / "chromium-1228"]
    assert (target / "chromium-1228" / "chrome").read_text(encoding="utf-8") == "browser"


def test_copy_installations_rejects_incomplete_browser_cache(tmp_path):
    module = _load_module()
    incomplete = tmp_path / "chromium-1228"
    incomplete.mkdir()

    with pytest.raises(RuntimeError, match="incomplete"):
        module.copy_installations([incomplete], tmp_path / "target")
