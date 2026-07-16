from crm_storage.factory import reset_store_for_tests, select_store_class


def test_without_database_url_uses_file_store(monkeypatch, tmp_path):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("CRM_DATA_DIR", str(tmp_path))
    reset_store_for_tests()
    assert select_store_class().__name__ == "FileStore"


def test_database_url_uses_postgres_store(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://crm:test@127.0.0.1:55432/crm_test")
    reset_store_for_tests()
    assert select_store_class().__name__ == "PostgresStore"
