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
# workspace; a copy of the backup's is a crash it should clean up.
SCRATCH_PREFIX = "trademind_scratch_"

LIVE_DB = "trademind"
SUPERUSER = os.environ.get("POSTGRES_SUPERUSER", "trademind")


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
    databases to destroy must be unable to destroy the one that matters, and
    that includes the backup's template, which only a superuser can rebuild.
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

def _compose(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "compose", *args],
        cwd=REPO_ROOT, capture_output=True, text=True, check=False,
    )


def _in_db_container(*command: str) -> list[str]:
    return ["docker", "compose", "exec", "-T", "db", *command]


def _existing() -> list[str]:
    result = _compose(
        "exec", "-T", "db", "psql", "-U", SUPERUSER, "-d", LIVE_DB, "-tAc",
        f"SELECT datname FROM pg_database WHERE datname LIKE '{SCRATCH_PREFIX}%' ORDER BY 1",
    )
    if result.returncode != 0:
        raise RuntimeError(f"could not list databases: {result.stderr.strip()}")
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _drop(database: str) -> None:
    assert_scratch(database)
    dropped = _compose("exec", "-T", "db", "dropdb", "-U", SUPERUSER, "--if-exists", database)
    if dropped.returncode != 0:
        raise RuntimeError(f"could not drop {database}: {dropped.stderr.strip()}")
    print(f"dropped {database}")


def _create_from(dump: Path, database: str) -> None:
    created = _compose("exec", "-T", "db", "createdb", "-U", SUPERUSER, database)
    if created.returncode != 0:
        raise RuntimeError(f"could not create {database}: {created.stderr.strip()}")

    with dump.open("rb") as handle:
        restored = subprocess.run(
            _in_db_container(
                "pg_restore", "-U", SUPERUSER, "-d", database,
                # As the superuser, so ownership and grants in the dump could be
                # replayed — but they name the live database's roles and this
                # copy gets its own below, so replaying them would only produce
                # errors about roles that already own things elsewhere.
                "--no-owner", "--no-acl", "--exit-on-error",
            ),
            cwd=REPO_ROOT, stdin=handle, capture_output=True, text=True, check=False,
        )
    if restored.returncode != 0:
        # Leave nothing half-restored behind pretending to be a copy.
        _drop(database)
        raise RuntimeError(f"pg_restore failed: {restored.stderr.strip()[:400]}")


def _apply_roles(database: str) -> None:
    """
    Give the copy the same three roles the live database has.

    Without this the application cannot read it at all: a restored database is
    owned by whoever restored it and carries no grants (ADR 0004). Doing it here
    is the difference between a copy you can point the app at and a puzzle.
    """
    url = os.environ.get(
        "SUPERUSER_DATABASE_URL",
        f"postgresql://{SUPERUSER}:trademind@localhost:5432/{LIVE_DB}",
    )
    scratch_url = url.rsplit("/", 1)[0] + f"/{database}"
    result = subprocess.run(
        [sys.executable, str(Path(__file__).parent / "setup_db_roles.py")],
        cwd=Path(__file__).parents[1],
        env={**os.environ, "SUPERUSER_DATABASE_URL": scratch_url},
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"could not set up roles on {database}: {result.stderr.strip()[:400]}")


def _counts(database: str) -> str:
    result = _compose(
        "exec", "-T", "db", "psql", "-U", SUPERUSER, "-d", database, "-tAc",
        "SELECT 'evidence='||(SELECT count(*) FROM evidence_items)"
        "||' ideas='||(SELECT count(*) FROM trade_ideas)"
        "||' usage='||(SELECT count(*) FROM usage_events)"
        "||' rev='||(SELECT version_num FROM alembic_version)",
    )
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
    print(f"  TEST_DATABASE_URL=postgresql+asyncpg://trademind_app:trademind@localhost:5432/{database}")
    print(f"  psql: docker compose exec db psql -U {SUPERUSER} -d {database}")
    print("\nThrow it away:")
    print(f"  python scripts/scratch_db.py --drop {database}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
