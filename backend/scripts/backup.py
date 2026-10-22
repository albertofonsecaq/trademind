#!/usr/bin/env python
"""
Takes a verified dump of the database on a schedule, and prunes old ones (#51).

    python scripts/backup.py                 # the scheduled job does this
    python scripts/backup.py --dry-run       # say what would happen, touch nothing
    python scripts/backup.py --prune-only    # apply the retention rule and stop

What is at stake is not reproducible by re-running anything: the database is
months of paid ingestion, and some of the Telegram history behind it may no
longer be fetchable. Until this existed the only protection was three dumps
somebody took by hand.

Three rules this follows, each of them learned from how backups usually fail:

1. A dump that has not been restored is not a backup. Every dump is restored
   into a throwaway database and counted before it is kept, and a dump that
   fails that is renamed so it cannot be mistaken for a good one.
2. A partial dump must never look like a good one, so dumps are written to a
   temporary name and moved into place only once pg_dump has succeeded.
3. Pruning only ever considers files this script wrote. Dumps taken by hand are
   somebody's deliberate snapshot — usually taken before something risky — and
   nothing here will delete one.

It reads the live database and never writes to it. The only database it creates
or drops is its own throwaway, whose name is checked before every drop.
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKUP_DIR = REPO_ROOT / "backups"
LOG_FILE = BACKUP_DIR / "backup.log"
FAILURE_MARKER = BACKUP_DIR / "LAST-RUN-FAILED"

LIVE_DB = "trademind"
DB_USER = "trademind"

# Every throwaway database this script makes starts with this, and nothing is
# dropped whose name does not. The live database is one typo away from a drop
# command otherwise, and there is no undo for that.
VERIFY_DB_PREFIX = "trademind_verify_"

# Dumps this script wrote. The -auto suffix is what separates them from one
# somebody took by hand, which the retention rule must never remove.
_SCHEDULED = re.compile(
    r"^trademind-(?P<stamp>\d{8}T\d{6}Z)-alembic(?P<revision>[0-9a-z]+)-auto\.dump$"
)

# Counted on both sides of a restore. Not every table: these are the ones whose
# loss would be unrecoverable, plus enough of the small ones to notice a dump
# that restored a schema and no data.
COUNTED_TABLES = (
    "evidence_items",
    "trade_ideas",
    "usage_events",
    "strategy_cards",
    "source_configs",
    "platform_connections",
    "workspaces",
    "users",
)

# A dump of this database with either of these empty is not a dump worth having.
MUST_NOT_BE_EMPTY = ("evidence_items", "usage_events")

KEEP_DAILY = 7
KEEP_WEEKLY = 4

log = logging.getLogger("backup")


# ── the parts worth testing on their own ─────────────────────────────────────

def dump_name(taken_at: datetime, revision: str) -> str:
    """
    What a scheduled dump is called.

    The migration revision is in the name because a dump only restores cleanly
    against code that expects its schema — it is the thing you need to know
    before trusting one, which is why the hand-taken dumps carry it too.
    """
    return f"trademind-{taken_at.strftime('%Y%m%dT%H%M%SZ')}-alembic{revision}-auto.dump"


def taken_at(name: str) -> datetime | None:
    """When this scheduled dump was taken, or None if this is not one of ours."""
    match = _SCHEDULED.match(name)
    if not match:
        return None
    return datetime.strptime(match.group("stamp"), "%Y%m%dT%H%M%SZ").replace(
        tzinfo=timezone.utc
    )


def prune_plan(
    names: list[str], *, keep_daily: int = KEEP_DAILY, keep_weekly: int = KEEP_WEEKLY
) -> list[str]:
    """
    Which files to delete, under "keep the last 7, and the last 4 Sundays".

    The second half is the one that matters: seven days of history only helps
    with damage noticed inside a week, and the whole reason this exists is that
    nobody was watching. Four Sundays mean a month of coarse history for the
    cost of four files.

    Anything this script did not write is never returned, however old it is.
    """
    ours = sorted(
        (name for name in names if taken_at(name) is not None),
        key=lambda name: taken_at(name),            # type: ignore[arg-type]
        reverse=True,
    )
    keep = set(ours[:keep_daily])

    sundays = [name for name in ours if taken_at(name).weekday() == 6]   # type: ignore[union-attr]
    keep.update(sundays[:keep_weekly])

    return [name for name in ours if name not in keep]


def verification_verdict(restored: dict[str, int], live: dict[str, int]) -> str | None:
    """
    Why this restored dump cannot be trusted, or None if it can.

    Deliberately not an equality check against the live database. The dump is a
    snapshot taken minutes earlier and ingestion carries on, so equal counts are
    not something a passing run can promise — demanding them would produce a
    failure every time the poller did its job, and an alarm that cries wolf is
    one nobody reads.

    So: everything that should be there is there, nothing that must not be empty
    is empty, and no table came back with more rows than the live database has.
    That last one would mean the dump is not of this database.
    """
    missing = [table for table in COUNTED_TABLES if table not in restored]
    if missing:
        return f"restored dump is missing tables: {', '.join(missing)}"

    empty = [table for table in MUST_NOT_BE_EMPTY if restored.get(table, 0) == 0]
    if empty:
        return f"restored dump has no rows in: {', '.join(empty)}"

    for table in COUNTED_TABLES:
        if table in live and restored[table] > live[table]:
            return (
                f"restored dump has more rows than the live database in {table} "
                f"({restored[table]} > {live[table]}), so it is not a dump of it"
            )
    return None


# ── talking to the database ──────────────────────────────────────────────────

def _compose(*args: str, capture: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "compose", *args],
        cwd=REPO_ROOT, capture_output=capture, text=True, check=False,
    )


def _psql(database: str, sql: str) -> str:
    result = _compose("exec", "-T", "db", "psql", "-U", DB_USER, "-d", database, "-tAc", sql)
    if result.returncode != 0:
        raise RuntimeError(f"psql failed on {database}: {result.stderr.strip()}")
    return result.stdout.strip()


def _counts(database: str) -> dict[str, int]:
    """Row counts for the tables we compare, in one round trip."""
    sql = " UNION ALL ".join(
        f"SELECT '{table}', count(*) FROM {table}" for table in COUNTED_TABLES
    )
    counts: dict[str, int] = {}
    for line in _psql(database, sql).splitlines():
        table, _, number = line.partition("|")
        if table:
            counts[table] = int(number)
    return counts


def _revision() -> str:
    return _psql(LIVE_DB, "SELECT version_num FROM alembic_version") or "unknown"


def _ensure_database_is_up() -> None:
    """
    Start the database if it is not running.

    A 03:00 job on a laptop finds it stopped more often than not, and a backup
    that quietly does nothing because the container was down is the failure this
    whole script exists to prevent.
    """
    started = _compose("up", "-d", "db")
    if started.returncode != 0:
        raise RuntimeError(f"could not start the database: {started.stderr.strip()}")
    for _ in range(30):
        ready = _compose("exec", "-T", "db", "pg_isready", "-U", DB_USER)
        if ready.returncode == 0:
            return
        subprocess.run(["sleep", "2"], check=False)
    raise RuntimeError("the database did not become ready")


def _drop_throwaway(database: str) -> None:
    """Drop a database, having first refused to drop anything but a throwaway."""
    if not database.startswith(VERIFY_DB_PREFIX) or database == LIVE_DB:
        raise RuntimeError(f"refusing to drop {database!r}: not a throwaway name")
    _compose("exec", "-T", "db", "dropdb", "-U", DB_USER, "--if-exists", database)


def _verify(dump: Path, live: dict[str, int]) -> dict[str, int]:
    """
    Restore the dump into a throwaway database and count it. Raises if it cannot
    be trusted; returns its counts if it can.
    """
    database = f"{VERIFY_DB_PREFIX}{os.getpid()}"
    _drop_throwaway(database)                       # a previous crash may have left one
    created = _compose("exec", "-T", "db", "createdb", "-U", DB_USER, database)
    if created.returncode != 0:
        raise RuntimeError(f"could not create {database}: {created.stderr.strip()}")
    try:
        with dump.open("rb") as handle:
            restored = subprocess.run(
                ["docker", "compose", "exec", "-T", "db",
                 "pg_restore", "-U", DB_USER, "-d", database, "--no-owner", "--exit-on-error"],
                cwd=REPO_ROOT, stdin=handle, capture_output=True, text=True, check=False,
            )
        if restored.returncode != 0:
            raise RuntimeError(f"pg_restore failed: {restored.stderr.strip()[:400]}")

        counts = _counts(database)
        reason = verification_verdict(counts, live)
        if reason:
            raise RuntimeError(reason)
        return counts
    finally:
        _drop_throwaway(database)


# ── the run ──────────────────────────────────────────────────────────────────

def _prune(directory: Path, *, dry_run: bool) -> list[str]:
    if not directory.is_dir():
        return []
    doomed = prune_plan([path.name for path in directory.glob("*.dump")])
    for name in doomed:
        log.info("pruning %s/%s", directory.name, name)
        if not dry_run:
            (directory / name).unlink()
    return doomed


def _mirror(dump: Path, *, dry_run: bool) -> None:
    """
    Copy the verified dump somewhere that does not depend on this machine.

    Configured rather than chosen here: an external disk one person uses and a
    synced folder another does are the same copy as far as this is concerned.
    An unset destination is a loud warning and not a failure — the local dump is
    still worth having, and a backup script that refuses to run because a disk
    is unplugged protects nobody.
    """
    destination = os.environ.get("BACKUP_MIRROR_DIR", "").strip()
    if not destination:
        log.warning(
            "BACKUP_MIRROR_DIR is not set, so this dump exists only on this machine. "
            "Losing the disk loses the backups with it."
        )
        return

    mirror = Path(destination).expanduser()
    if not mirror.is_dir():
        log.error("BACKUP_MIRROR_DIR %s is not a directory — nothing copied off this machine", mirror)
        return

    log.info("copying to %s", mirror)
    if not dry_run:
        # Copy beside the final name, then move: an interrupted copy must not
        # leave something that looks like a complete dump in the one place we
        # would reach for after losing this machine.
        partial = mirror / f".{dump.name}.partial"
        shutil.copy2(dump, partial)
        partial.replace(mirror / dump.name)
    _prune(mirror, dry_run=dry_run)


def _notify(message: str) -> None:
    """Best-effort desktop notification: a failed backup nobody sees is silent."""
    if not shutil.which("osascript"):
        return
    subprocess.run(
        ["osascript", "-e",
         f'display notification {message!r} with title "TradeMind backup failed"'],
        capture_output=True, check=False,
    )


def run(*, dry_run: bool = False, prune_only: bool = False) -> int:
    BACKUP_DIR.mkdir(exist_ok=True)

    if prune_only:
        _prune(BACKUP_DIR, dry_run=dry_run)
        return 0

    _ensure_database_is_up()
    revision = _revision()
    live = _counts(LIVE_DB)
    log.info("live database at revision %s: %s", revision, live)

    final = BACKUP_DIR / dump_name(datetime.now(timezone.utc), revision)
    if dry_run:
        log.info("would write %s, verify it by restore, then prune", final.name)
        _prune(BACKUP_DIR, dry_run=True)
        _mirror(final, dry_run=True)
        return 0

    # Written under a name nothing else recognises, so a dump interrupted
    # halfway through can never be counted as a backup.
    partial = BACKUP_DIR / f".{final.name}.partial"
    with partial.open("wb") as handle:
        dumped = subprocess.run(
            ["docker", "compose", "exec", "-T", "db",
             "pg_dump", "-U", DB_USER, "-d", LIVE_DB, "-Fc"],
            cwd=REPO_ROOT, stdout=handle, stderr=subprocess.PIPE, text=False, check=False,
        )
    if dumped.returncode != 0:
        partial.unlink(missing_ok=True)
        raise RuntimeError(f"pg_dump failed: {dumped.stderr.decode()[:400]}")
    partial.replace(final)
    log.info("wrote %s (%.1f MB)", final.name, final.stat().st_size / 1e6)

    try:
        counts = _verify(final, live)
    except Exception:
        # Kept, not deleted: a dump that failed verification is evidence about
        # why, and the name makes it unmistakable meanwhile.
        failed = final.with_suffix(".dump.FAILED-VERIFICATION")
        final.replace(failed)
        log.error("verification failed — kept as %s", failed.name)
        raise
    log.info("verified by restore: %s", counts)

    _mirror(final, dry_run=False)
    _prune(BACKUP_DIR, dry_run=False)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="say what would happen")
    parser.add_argument("--prune-only", action="store_true", help="apply retention and stop")
    args = parser.parse_args()

    BACKUP_DIR.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stdout)],
    )

    try:
        code = run(dry_run=args.dry_run, prune_only=args.prune_only)
    except Exception as e:
        log.error("backup failed: %s", e)
        FAILURE_MARKER.write_text(
            f"{datetime.now(timezone.utc).isoformat()}\n{e}\n"
            f"See {LOG_FILE.name}. Fix it and run scripts/backup.py by hand.\n"
        )
        _notify(str(e)[:200])
        return 1

    # Cleared only by a run that got all the way through, so the marker's
    # presence always means "the last attempt did not finish".
    FAILURE_MARKER.unlink(missing_ok=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
