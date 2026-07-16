# Task 1 Report: Storage Contract and Backend Selection

## What changed

- Added the `ReportRecord`, `EntityRecord`, and `StorageBackend` contract in `crm_storage/base.py`.
- Added lazy `DATABASE_URL`-based backend selection and process-wide store caching in `crm_storage/factory.py`.
- Added constructor-only `FileStore` and `PostgresStore` stubs.
- Exported the storage contract and factory API from `crm_storage/__init__.py`.
- Added development requirements and the focused backend-selection test harness.

## RED

Command:

```text
.venv/bin/python -m pytest tests/test_storage_factory.py -q
```

Output:

```text
E   ModuleNotFoundError: No module named 'crm_storage'
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
```

The initial `python -m pytest ...` command could not run because this shell has no `python` executable. Pytest was installed into the worktree `.venv` before rerunning the required check.

## GREEN

Command:

```text
.venv/bin/python -m pytest tests/test_storage_factory.py -q
```

Output:

```text
..                                                                       [100%]
2 passed in 0.01s
```

## Tests and checks

- `.venv/bin/python -m pytest tests/test_storage_factory.py -q`: passed, 2 tests.
- `.venv/bin/python -m pytest -q`: passed, 2 tests.
- `.venv/bin/python -m compileall -q app.py crm_storage`: passed.
- `git diff --check`: passed with no output.

## Files

- `crm_storage/__init__.py`
- `crm_storage/base.py`
- `crm_storage/factory.py`
- `crm_storage/file_store.py`
- `crm_storage/postgres_store.py`
- `requirements-dev.txt`
- `tests/conftest.py`
- `tests/test_storage_factory.py`

## Self-review

- Confirmed backend imports remain lazy inside `select_store_class()`.
- Confirmed `get_store()` uses the process-wide cache and passes `DATABASE_URL` or `CRM_DATA_DIR` to the selected constructor.
- Confirmed reset behavior clears the cached backend and calls an available `close()` method.
- Confirmed no real database connection or application integration was added.
- Confirmed the diff is limited to the requested task files plus this report.

## Concerns

- The broader baseline command from the migration plan, `.venv/bin/python -m compileall -q app.py crm_storage scripts/migrate_files_to_postgres.py`, reports `Can't list 'scripts/migrate_files_to_postgres.py'` because that later-task script does not exist yet. The task-scoped compile check passes.
