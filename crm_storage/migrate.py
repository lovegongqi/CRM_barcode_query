from pathlib import Path


MIGRATIONS_DIR = Path(__file__).with_name("migrations")
MIGRATION_LOCK_KEY = 742382475


def run_migrations(connection):
    with connection.transaction():
        connection.execute("select pg_advisory_xact_lock(%s)", (MIGRATION_LOCK_KEY,))
        migration_table = connection.execute("select to_regclass('public.schema_migrations')").fetchone()[0]
        applied_versions = set()
        if migration_table:
            applied_versions = {row[0] for row in connection.execute("select version from schema_migrations")}

        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name in applied_versions:
                continue
            connection.execute(path.read_text(encoding="utf-8"))
            connection.execute("insert into schema_migrations (version) values (%s)", (path.name,))
