from pathlib import Path


MIGRATIONS_DIR = Path(__file__).with_name("migrations")


def run_migrations(connection):
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        with connection.transaction():
            migration_table = connection.execute("select to_regclass('public.schema_migrations')").fetchone()[0]
            if migration_table:
                applied = connection.execute(
                    "select 1 from schema_migrations where version = %s", (path.name,)
                ).fetchone()
                if applied:
                    continue

            connection.execute(path.read_text(encoding="utf-8"))
            connection.execute("insert into schema_migrations (version) values (%s)", (path.name,))
