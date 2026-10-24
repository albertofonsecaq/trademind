#!/usr/bin/env python
"""
Splits the one all-owning database role into three, so the credentials the
application carries cannot destroy anything (#59).

    python scripts/setup_db_roles.py                 # apply, idempotently
    python scripts/setup_db_roles.py --dry-run       # print the statements
    python scripts/setup_db_roles.py --show          # report who owns what now
    python scripts/setup_db_roles.py --revert        # hand ownership back

On 2026-10-22 this database was dropped by a tool holding the application's own
credentials. One role owned the database and every object in it, and that role
was in every connection string in the repo, so the only thing between any
process and the whole knowledge base was an `if` statement in a Python helper.

Three roles, because there are three jobs:

    owner    owns the database and every object; used by `alembic upgrade` only
    app      reads and writes rows; owns nothing, creates nothing, drops nothing
    backup   reads everything and can create its own databases, so it can dump
             and verify by restore — and cannot drop the live one

Runs as the bootstrap superuser, which is the only role that can hand ownership
away. It is idempotent: safe to run on a database that already has the roles,
which is what makes it usable as a setup step rather than a one-off ritual.

**What this does not do.** The bootstrap superuser still exists and still trusts
local socket connections, so somebody with shell and Docker access can still
drop the database. Closing that needs a second superuser to exist first, or the
cluster has no way back from a mistake. What this closes is the path that was
actually taken: nothing the application, the migrations or the scripts carry can
destroy anything. See docs/adr/0004 and backups/README.md.
"""
from __future__ import annotations

import argparse
import os
import sys
from urllib.parse import urlparse

OWNER = "trademind_owner"
APP = "trademind_app"
BACKUP = "trademind_backup"

# The backup verifies a dump by restoring it, and a dump of this database
# replays `CREATE EXTENSION vector`, which only a superuser may do. So the
# throwaway it restores into is copied from this template, which already has the
# extension: `CREATE EXTENSION IF NOT EXISTS` on an extension that exists is a
# notice rather than a permission check. The template is owned by the backup
# role, because copying a database requires owning the one you copy.
VERIFY_TEMPLATE = "trademind_verify_template"

# The bootstrap role the container creates, which owns everything until this
# runs. Named here because it is the role whose ownership is handed away.
BOOTSTRAP = os.environ.get("POSTGRES_SUPERUSER", "trademind")


def _password(role: str) -> str:
    """
    The password for one role.

    Defaulted for local development and CI, exactly as DATABASE_URL is: a
    database reachable only on a loopback port, holding nothing until somebody
    ingests into it. Anywhere else, set these.
    """
    variable = f"{role.upper()}_PASSWORD"
    return os.environ.get(variable, "trademind")


def statements(database: str) -> list[str]:
    """
    Every statement, in the order they have to run. Returned rather than
    executed so --dry-run can print exactly what would happen to a database
    nobody can replace.
    """
    plan = [
        # Before the roles: the extension needs a superuser, and migration 001
        # only creates it IF NOT EXISTS — so doing it here is what lets the
        # migrations run as an owner that is not a superuser.
        "CREATE EXTENSION IF NOT EXISTS vector",
    ]

    for role in (OWNER, APP, BACKUP):
        # A role may already exist from an earlier run; the password is set
        # either way, so rotating one is the same command as creating it.
        plan.append(
            f"DO $$ BEGIN "
            f"IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') "
            f"THEN CREATE ROLE {role} LOGIN; END IF; END $$"
        )
        plan.append(f"ALTER ROLE {role} WITH LOGIN PASSWORD '{_password(role)}'")

    plan += [
        # The database itself.
        f"ALTER DATABASE {database} OWNER TO {OWNER}",

        # And every object in public, one at a time rather than with REASSIGN
        # OWNED: the bootstrap role also owns objects the database system
        # requires, and REASSIGN cannot separate those from ours — it refuses
        # the lot with "required by the database system". Found by rehearsing
        # this on a scratch copy before running it on the real database.
        #
        # Objects belonging to an extension are skipped: pgvector's functions
        # and operators live in public and are the extension's to own, not ours.
        # The migrations need ownership of the tables they alter; they never
        # alter pgvector.
        f"""
        DO $$
        DECLARE relation record;
        BEGIN
          FOR relation IN
            SELECT c.relname, c.relkind
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public'
              AND c.relkind IN ('r', 'p', 'S', 'v', 'm')
              AND NOT EXISTS (
                SELECT 1 FROM pg_depend d WHERE d.objid = c.oid AND d.deptype = 'e'
              )
          LOOP
            EXECUTE format(
              'ALTER %s %I OWNER TO %I',
              CASE relation.relkind
                WHEN 'r' THEN 'TABLE' WHEN 'p' THEN 'TABLE'
                WHEN 'S' THEN 'SEQUENCE'
                WHEN 'v' THEN 'VIEW' WHEN 'm' THEN 'MATERIALIZED VIEW'
              END,
              relation.relname, '{OWNER}'
            );
          END LOOP;
        END $$
        """,

        # Nobody creates objects in public except the owner. Without this,
        # PUBLIC keeps the CREATE it has by default on older clusters, and the
        # application could leave a table behind for a migration to collide with.
        "REVOKE CREATE ON SCHEMA public FROM PUBLIC",
        f"GRANT USAGE ON SCHEMA public TO {APP}, {BACKUP}",
        f"GRANT ALL ON SCHEMA public TO {OWNER}",

        # What the application actually does: rows, and the sequences behind
        # them. Not TRUNCATE — it is a DELETE nobody can audit.
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP}",
        f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP}",

        # The same for whatever the next migration adds. Without this, every
        # new table is invisible to the application, and that failure arrives a
        # deploy after the change that caused it.
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {OWNER} IN SCHEMA public "
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {APP}",
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {OWNER} IN SCHEMA public "
        f"GRANT USAGE, SELECT ON SEQUENCES TO {APP}",

        # The backup reads everything — including tables added later, which is
        # why this is the built-in role and not a list of grants.
        f"GRANT pg_read_all_data TO {BACKUP}",
        f"ALTER ROLE {BACKUP} WITH CREATEDB",

        # Explicitly not granted anywhere: SUPERUSER, CREATEROLE, and CREATEDB
        # for the application.
        f"ALTER ROLE {APP} WITH NOSUPERUSER NOCREATEDB NOCREATEROLE",
        f"ALTER ROLE {OWNER} WITH NOSUPERUSER NOCREATEROLE",
    ]
    return plan


def revert_statements(database: str) -> list[str]:
    """
    Hand everything back to the bootstrap role.

    Here because a change to ownership on a database nobody can replace needs a
    way back that has been tried, not improvised at the point it is needed. It
    leaves the roles in place: dropping a role that still owns something fails,
    and a role nothing connects as costs nothing.

    Reverting means the application's credentials can destroy the database
    again, so this is for getting out of trouble, not for staying anywhere.
    """
    return [
        f"ALTER DATABASE {database} OWNER TO {BOOTSTRAP}",
        f"""
        DO $$
        DECLARE relation record;
        BEGIN
          FOR relation IN
            SELECT c.relname, c.relkind
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public'
              AND c.relkind IN ('r', 'p', 'S', 'v', 'm')
              AND NOT EXISTS (
                SELECT 1 FROM pg_depend d WHERE d.objid = c.oid AND d.deptype = 'e'
              )
          LOOP
            EXECUTE format(
              'ALTER %s %I OWNER TO %I',
              CASE relation.relkind
                WHEN 'r' THEN 'TABLE' WHEN 'p' THEN 'TABLE'
                WHEN 'S' THEN 'SEQUENCE'
                WHEN 'v' THEN 'VIEW' WHEN 'm' THEN 'MATERIALIZED VIEW'
              END,
              relation.relname, '{BOOTSTRAP}'
            );
          END LOOP;
        END $$
        """,
    ]


def template_statements() -> list[str]:
    """
    The verify template, which has to be created outside the main connection:
    CREATE DATABASE cannot run against the database it is creating, and the
    extension has to be created while connected to the new one.
    """
    return [
        f"CREATE DATABASE {VERIFY_TEMPLATE} OWNER {BACKUP}",
        # run against VERIFY_TEMPLATE, not the live database
        "CREATE EXTENSION IF NOT EXISTS vector",
    ]


def _report(cursor) -> None:
    cursor.execute("""
        SELECT rolname, rolsuper, rolcreatedb,
               pg_has_role(rolname, 'pg_read_all_data', 'MEMBER')
        FROM pg_roles WHERE rolname NOT LIKE 'pg\\_%' ORDER BY rolname
    """)
    print(f"{'role':<22} {'super':<7} {'createdb':<9} reads-all")
    for name, super_, createdb, reads in cursor.fetchall():
        print(f"{name:<22} {str(super_):<7} {str(createdb):<9} {reads}")

    cursor.execute("SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = current_database()")
    print(f"\ndatabase owner: {cursor.fetchone()[0]}")
    cursor.execute("SELECT DISTINCT tableowner FROM pg_tables WHERE schemaname = 'public'")
    print(f"table owners:   {', '.join(row[0] for row in cursor.fetchall())}")


def _connect(url: str):
    """
    A connection in autocommit, without psycopg2's `with` block.

    Deliberately not `with psycopg2.connect(...)`: that block holds a
    transaction open, so setting autocommit inside it has no effect and
    CREATE DATABASE is refused with "cannot run inside a transaction block".
    Everything else here happens to be legal inside a transaction, so this
    only showed up on the one statement that is not.
    """
    import psycopg2

    connection = psycopg2.connect(url)
    connection.autocommit = True
    return connection


def _ensure_verify_template(superuser_url: str) -> None:
    """Create the verify template if it is not there, and put the extension in it."""
    from psycopg2 import errors

    create, extension = template_statements()

    connection = _connect(superuser_url)
    try:
        with connection.cursor() as cursor:
            try:
                cursor.execute(create)
                print(f"created {VERIFY_TEMPLATE}")
            except errors.DuplicateDatabase:
                pass            # already there; this script is run repeatedly
    finally:
        connection.close()

    connection = _connect(superuser_url.rsplit("/", 1)[0] + f"/{VERIFY_TEMPLATE}")
    try:
        with connection.cursor() as cursor:
            cursor.execute(extension)
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--dry-run", action="store_true", help="print the statements, change nothing")
    parser.add_argument("--show", action="store_true", help="report the current roles and owners")
    parser.add_argument(
        "--revert", action="store_true",
        help="hand ownership back to the bootstrap role (see revert_statements)",
    )
    parser.add_argument(
        "--url",
        default=os.environ.get(
            "SUPERUSER_DATABASE_URL",
            "postgresql://trademind:trademind@localhost:5432/trademind",
        ),
        help="connection as the bootstrap superuser",
    )
    args = parser.parse_args()

    database = urlparse(args.url).path.lstrip("/") or "trademind"
    plan = revert_statements(database) if args.revert else statements(database)

    if args.dry_run:
        for statement in plan:
            print(f"{statement};")
        return 0

    connection = _connect(args.url)
    try:
        with connection.cursor() as cursor:
            if args.show:
                _report(cursor)
                return 0
            for statement in plan:
                cursor.execute(statement)
            print(f"applied {len(plan)} statements")
    finally:
        connection.close()

    if args.revert:
        connection = _connect(args.url)
        try:
            with connection.cursor() as cursor:
                _report(cursor)
        finally:
            connection.close()
        return 0

    _ensure_verify_template(args.url)

    connection = _connect(args.url)
    try:
        with connection.cursor() as cursor:
            _report(cursor)
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
