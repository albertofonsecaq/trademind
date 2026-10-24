#!/usr/bin/env python
"""
A disposable copy of the real database, restored from the newest verified dump.

    python scripts/scratch_db.py                 # make one, print how to use it
    python scripts/scratch_db.py --list          # what copies exist
    python scripts/scratch_db.py --drop <name>   # remove one
    python scripts/scratch_db.py --dump <path>   # copy a particular dump instead

Point anything destructive at one of these. Dropping a table to see whether a
permission holds, exercising a guard, trying a migration's downgrade, rehearsing
a restore: all of it belongs here rather than against the only copy of months of
paid ingestion (#60).

That is not a style preference. On 2026-10-22 a tool ran a `dropdb` guard
against the live database to find out whether the guard worked, and destroyed
it. The guard was correct. The reason production was what got used is that there
was nowhere else to go — so this exists to be the obvious somewhere else.

**It never writes to the live database.** It reads a dump file, creates a new
database, restores into it, and gives it the same roles the live one has. The
only `dropdb` it will run is against a database whose name it made itself.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND.parent
BACKUP_DIR = REPO_ROOT / "backups"

# So `from scripts.backup import ...` works when this is run as a script and
# not only under pytest, whose conftest already puts backend on the path.
# Resolved from __file__ rather than the working directory, because this is run
# from the repo root as often as from backend/.
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

# Deliberately not the backup's "trademind_verify_" prefix: these are two
# different kinds of throwaway with two different owners, and each script
# refuses to drop the other's. A copy of this one lying around is somebody's
# working copy; a copy of the backup's is a crash it should clean up.
SCRATCH_PREFIX = "trademind_scratch_"

LIVE_DB = "trademind"
BOOTSTRAP = os.environ.get("POSTGRES_SUPERUSER", "trademind")


def _password(role: str) -> str:
    """
    One role's password, from the variables .env.example documents.

    Read rather than assumed, because the connection string this script prints
    is the whole point of it — and a printed string that silently fails to
    authenticate anywhere the passwords were set is worse than none.
    """
    if role == "superuser":
        return os.environ.get("POSTGRES_PASSWORD", "trademind")
    from scripts.setup_db_roles import password_variable

    return os.environ.get(password_variable(role), "trademind")

# Everything destructive here runs as the backup role, which can create its own
# databases and read every table and *cannot drop the live one*. The first
# version of this used the bootstrap superuser, which made an `if` the only
# thing standing between this script and the database again — the exact
# arrangement ADR 0004 removed, in the script written to stop people needing it.
#
# The superuser is used for one thing: granting the copy its roles, which is not
# a destructive operation and cannot be done by a role that owns nothing.
SCRATCH_USER = os.environ.get("SCRATCH_DB_USER", "trademind_backup")

# Copied from the template rather than created empty, for the reason backup.py
# gives: a dump of this database replays CREATE EXTENSION vector, and only a
# superuser may do that.
from scripts.backup import VERIFY_TEMPLATE  # noqa: E402


def scratch_name(created_at: datetime) -> str:
    """
    What a copy is called.

    The timestamp is in the name so two people, or one person twice, do not
    collide — and so a copy left running for a week is obviously a week old.
    """
    return f"{SCRATCH_PREFIX}{created_at.strftime('%Y%m%d_%H%M%S')}"


def assert_scratch(database: str) -> None:
    """
    Refuse any name this script did not make.

    The one irreversible command in here. A script whose purpose is handing out
    databases to destroy must be unable to destroy the one that matters.

    The prefix does all the work, including keeping this away from the backup's
    `trademind_verify_*` names and its template. The explicit live-database
    clause below is redundant — "trademind" cannot start with
    "trademind_scratch_" — and stays as the thing a future rename would trip
    over, since the cost of it being here is one comparison.
    """
    if not database.startswith(SCRATCH_PREFIX) or database == LIVE_DB:
        raise RuntimeError(
            f"refusing to drop {database!r}: not a scratch database. "
            f"Only names starting {SCRATCH_PREFIX!r} are this script's to remove."
        )


def newest_verified_dump(names: list[str]) -> str | None:
    """
    The newest dump known to restore, or None if there is none.

    "Known to restore" means the backup wrote it and still calls it a dump: it
    restores every dump into a throwaway and counts it before keeping it, and
    renames the ones that fail. So the suffix is the evidence (ADR 0003).

    A hand-taken dump is not a candidate. Nothing records whether one was
    verified, and silently handing somebody an unverified copy of the database
    is how this script would come to lie. Name one with --dump to use it
    anyway.
    """
    from scripts.backup import taken_at

    candidates = [name for name in names if taken_at(name) is not None]
    if not candidates:
        return None
    return max(candidates, key=lambda name: taken_at(name))      # type: ignore[arg-type]


# ── talking to the server ────────────────────────────────────────────────────

# Imported rather than copied: this file already imports from backup.py, so the
# independence that would justify a second copy is spent either way.
from scripts.backup import _compose, _in_db_container  # noqa: E402


def _existing() -> list[str]:
    # Connected to `postgres`, not to the live database: listing copies has to
    # work when the live one is the thing that is broken, which is exactly when
    # somebody wants one.
    result = _compose(
        "exec", "-T", "db", "psql", "-U", SCRATCH_USER, "-d", "postgres", "-tAc",
        f"SELECT datname FROM pg_database WHERE datname LIKE '{SCRATCH_PREFIX}%' ORDER BY 1",
    )
    if result.returncode != 0:
        raise RuntimeError(f"could not list databases: {result.stderr.strip()}")
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _drop(database: str) -> None:
    assert_scratch(database)
    dropped = _compose("exec", "-T", "db", "dropdb", "-U", SCRATCH_USER, "--if-exists", database)
    if dropped.returncode != 0:
        raise RuntimeError(f"could not drop {database}: {dropped.stderr.strip()}")
    print(f"dropped {database}")


def _create_from(dump: Path, database: str) -> None:
    created = _compose(
        "exec", "-T", "db", "createdb", "-U", SCRATCH_USER, "-T", VERIFY_TEMPLATE, database,
    )
    if created.returncode != 0:
        raise RuntimeError(
            f"could not create {database} from {VERIFY_TEMPLATE}: {created.stderr.strip()} "
            f"— run scripts/setup_db_roles.py if the template is missing"
        )

    with dump.open("rb") as handle:
        restored = subprocess.run(
            _in_db_container(
                "pg_restore", "-U", SCRATCH_USER, "-d", database,
                # Ownership, grants and comments all name things this role does
                # not own, and none of them matter to a copy that gets its own
                # roles below. Same three flags, same reasons, as the backup's
                # verification (ADR 0004).
                "--no-owner", "--no-acl", "--no-comments", "--exit-on-error",
            ),
            cwd=REPO_ROOT, stdin=handle, capture_output=True, text=True, check=False,
        )
    if restored.returncode != 0:
        # Leave nothing half-restored behind pretending to be a copy.
        _drop(database)
        raise RuntimeError(f"pg_restore failed: {restored.stderr.strip()[:400]}")


def superuser_url_for(database: str) -> str:
    """
    The superuser connection string, pointed at one database.

    Rebuilt through urlparse rather than by cutting at the last slash: that
    loses anything after the database name, so a URL carrying ?sslmode=require
    would quietly connect somewhere else. setup_db_roles.py had the same bug and
    the same fix; this is the shared seam, and it is tested.
    """
    from urllib.parse import urlparse

    url = os.environ.get(
        "SUPERUSER_DATABASE_URL",
        f"postgresql://{BOOTSTRAP}:{_password('superuser')}@localhost:5432/trademind",
    )
    return urlparse(url)._replace(path=f"/{database}").geturl()


def _apply_roles(database: str) -> None:
    """
    Give the copy the same three roles the live database has.

    Without this the application cannot read it at all: a restored database is
    owned by whoever restored it and carries no grants (ADR 0004). Doing it here
    is the difference between a copy you can point the app at and a puzzle.
    """
    scratch_url = superuser_url_for(database)
    result = subprocess.run(
        [sys.executable, str(Path(__file__).parent / "setup_db_roles.py")],
        cwd=Path(__file__).parents[1],
        env={**os.environ, "SUPERUSER_DATABASE_URL": scratch_url},
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"could not set up roles on {database}: {result.stderr.strip()[:400]}")

    # Setting up the roles hands the *database* to the owner role along with its
    # objects, which leaves this script unable to drop the copy it just made —
    # found by trying to. The two ownerships are separate: the objects stay with
    # the owner role, so migrations and grants behave exactly as they do on the
    # live database, and the database itself goes back to the role that created
    # it and has to be able to throw it away.
    handed_back = _compose(
        "exec", "-T", "db", "psql", "-U", BOOTSTRAP, "-d", "postgres", "-c",
        f'ALTER DATABASE "{database}" OWNER TO {SCRATCH_USER}',
    )
    if handed_back.returncode != 0:
        raise RuntimeError(
            f"could not hand {database} back to {SCRATCH_USER}: "
            f"{handed_back.stderr.strip()}"
        )


def _counts(database: str) -> str:
    """What the copy holds. Raises rather than returning an empty string: a
    success message about a copy nobody counted is the dishonesty ADR 0003 is
    about."""
    result = _compose(
        "exec", "-T", "db", "psql", "-U", SCRATCH_USER, "-d", database, "-tAc",
        "SELECT 'evidence='||(SELECT count(*) FROM evidence_items)"
        "||' ideas='||(SELECT count(*) FROM trade_ideas)"
        "||' usage='||(SELECT count(*) FROM usage_events)"
        "||' rev='||(SELECT version_num FROM alembic_version)",
    )
    if result.returncode != 0:
        raise RuntimeError(f"could not count {database}: {result.stderr.strip()}")
    return result.stdout.strip()


def main() -> int:
    try:
        return _run()
    except RuntimeError as refusal:
        # A refusal is an answer, not a crash. This is reached for when
        # something has already gone wrong, and a traceback is a poor way to
        # tell somebody they typed the wrong database name.
        print(str(refusal), file=sys.stderr)
        return 1


def _run() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--list", action="store_true", help="show the copies that exist")
    parser.add_argument("--drop", metavar="NAME", help="remove one copy")
    parser.add_argument("--dump", metavar="PATH", help="copy this dump instead of the newest")
    args = parser.parse_args()

    if args.list:
        existing = _existing()
        print("\n".join(existing) if existing else "no scratch databases")
        return 0

    if args.drop:
        _drop(args.drop)
        return 0

    if args.dump:
        dump = Path(args.dump)
        if not dump.is_file():
            print(f"No dump at {dump}", file=sys.stderr)
            return 1
    else:
        newest = newest_verified_dump([path.name for path in BACKUP_DIR.glob("*.dump")])
        if newest is None:
            print(
                "No verified dump to copy. Run scripts/backup.py first — a scratch\n"
                "database restored from nothing is an empty schema that looks like\n"
                "the real thing, which is worse than not having one.",
                file=sys.stderr,
            )
            return 1
        dump = BACKUP_DIR / newest

    database = scratch_name(datetime.now(timezone.utc))
    print(f"copying {dump.name} into {database} …")
    _create_from(dump, database)
    _apply_roles(database)

    print(f"\n{database} is ready: {_counts(database)}")
    print("\nPoint something at it:")
    app_password = _password("trademind_app")
    print(
        f"  TEST_DATABASE_URL=postgresql+asyncpg://"
        f"trademind_app:{app_password}@localhost:5432/{database}"
    )
    print(f"  psql: docker compose exec db psql -U {SCRATCH_USER} -d {database}")
    print("\nThrow it away:")
    # Spelled from the repo root, which is how the docs spell it and where
    # people paste from.
    print(f"  python backend/scripts/scratch_db.py --drop {database}")
    print(
        f"\nIt is a copy. Nothing you do to {database} touches the live database, "
        f"and this script will not drop anything that is not its own copy."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
