import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
COMPOSE_FILE = ROOT / "deployment" / "docker-compose.server.yml"


@pytest.fixture(scope="module")
def compose_config():
    if not shutil.which("docker"):
        pytest.skip("docker compose is required for the server Compose contract")
    env = os.environ.copy()
    env.update({
        "POSTGRES_PASSWORD": "test-postgres-password",
        "DATABASE_URL": "postgresql://crm:test-postgres-password@postgres:5432/crm",
        "CRM_CREDENTIALS_KEY": "bW1tbW1tbW1tbW1tbW1tbW1tbW1tbW1tbW1tbW1tbW0=",
        "FLASK_SECRET_KEY": "test-flask-secret",
    })
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(COMPOSE_FILE),
            "--profile",
            "migration",
            "config",
            "--format",
            "json",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _volume_targets(service):
    return {
        row["target"]: row["source"]
        for row in service.get("volumes", [])
        if row.get("type") == "volume"
    }


def test_server_compose_keeps_data_and_sessions_separate(compose_config):
    services = compose_config["services"]
    postgres = services["postgres"]
    app = services["crm-barcode-query"]

    assert postgres["image"] == "postgres:17"
    assert _volume_targets(postgres)["/var/lib/postgresql/data"] == "crm_app_postgres"
    assert _volume_targets(app)["/app/session"] == "crm_browser_session"
    assert _volume_targets(app)["/app/data"] == "crm_app_runtime"
    assert app["environment"]["DATABASE_URL"]
    assert not postgres.get("ports")


def test_server_compose_has_migration_and_health_contracts(compose_config):
    services = compose_config["services"]

    assert "migrate" in services
    assert "pg_isready" in " ".join(services["postgres"]["healthcheck"]["test"])
    assert "/healthz" in " ".join(
        services["crm-barcode-query"]["healthcheck"]["test"]
    )
    assert services["crm-barcode-query"]["depends_on"]["postgres"]["condition"] == "service_healthy"


def test_backup_scripts_are_syntax_checked_and_cover_encryption_and_restore_tables():
    backup = ROOT / "scripts" / "backup_postgres.sh"
    restore = ROOT / "scripts" / "verify_postgres_restore.sh"
    result = subprocess.run(
        ["bash", "-n", str(backup), str(restore)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr

    backup_text = backup.read_text(encoding="utf-8")
    restore_text = restore.read_text(encoding="utf-8")
    assert "pg_dump" in backup_text
    assert "--format=custom" in backup_text
    assert "BACKUP_AGE_RECIPIENT" in backup_text
    assert "age" in backup_text
    assert ".sha256" in backup_text
    assert "BACKUP_RETENTION_DAYS" in backup_text
    assert "pg_restore" in restore_text
    for table in (
        "schema_migrations",
        "barcode_reports",
        "app_entities",
        "operation_logs",
        "sync_events",
        "sync_cursors",
        "sync_tombstones",
    ):
        assert table in restore_text


def test_dockerfile_uses_prebuilt_multi_arch_playwright_runtime():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")

    assert dockerfile.startswith(
        "FROM mcr.microsoft.com/playwright/python:v1.61.0-noble\n"
    )
    assert "playwright==1.61.0" in requirements
    assert "playwright install --with-deps" not in dockerfile
    assert "PIP_DEFAULT_TIMEOUT=120" in dockerfile
    assert "PIP_RETRIES=5" in dockerfile
