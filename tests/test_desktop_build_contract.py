from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_desktop_builds_package_storage_and_allow_slow_browser_downloads():
    macos = (ROOT / "build_macos.sh").read_text(encoding="utf-8")
    windows = (ROOT / "build_windows.ps1").read_text(encoding="utf-8")

    assert "PLAYWRIGHT_DOWNLOAD_CONNECTION_TIMEOUT=120000" in macos
    assert 'PLAYWRIGHT_DOWNLOAD_CONNECTION_TIMEOUT = "120000"' in windows
    assert '--add-data "crm_storage/migrations:crm_storage/migrations"' in macos
    assert '--add-data "crm_storage/migrations;crm_storage/migrations"' in windows
    assert '--hidden-import crm_storage.postgres_store' in macos
    assert '--hidden-import crm_storage.postgres_store' in windows
    assert '--collect-all crm_storage' not in macos
    assert '--collect-all crm_storage' not in windows
    assert "scripts/install_packaged_chromium.py" in macos
    assert "scripts\\install_packaged_chromium.py" in windows


def test_desktop_workflows_watch_storage_and_desktop_dependencies():
    for workflow_name in ("macos-app.yml", "windows-exe.yml"):
        workflow = (ROOT / ".github" / "workflows" / workflow_name).read_text(
            encoding="utf-8"
        )
        assert '"crm_storage/**"' in workflow
        assert '"requirements-desktop.txt"' in workflow
        assert '"scripts/install_packaged_chromium.py"' in workflow


def test_windows_arm_runner_builds_x64_compatible_package():
    workflow = (ROOT / ".github" / "workflows" / "windows-exe.yml").read_text(
        encoding="utf-8"
    )

    arm_job = workflow.split("- arch: arm64", 1)[1]
    assert "runner: windows-11-arm" in arm_job
    assert "python-architecture: x64" in arm_job


def test_windows_build_stops_when_native_dependency_install_fails():
    windows = (ROOT / "build_windows.ps1").read_text(encoding="utf-8")

    assert '$PSNativeCommandUseErrorActionPreference = $true' in windows
