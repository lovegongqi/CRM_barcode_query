import os
import threading

_store = None
_lock = threading.Lock()


def select_store_class():
    if os.environ.get("DATABASE_URL"):
        from .postgres_store import PostgresStore
        return PostgresStore
    from .file_store import FileStore
    return FileStore


def get_store():
    global _store
    with _lock:
        if _store is None:
            store_class = select_store_class()
            argument = os.environ["DATABASE_URL"] if os.environ.get("DATABASE_URL") else os.environ.get("CRM_DATA_DIR")
            _store = store_class(argument)
        return _store


def reset_store_for_tests():
    global _store
    with _lock:
        close = getattr(_store, "close", None)
        if close:
            close()
        _store = None
