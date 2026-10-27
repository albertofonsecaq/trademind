#!/usr/bin/env python
"""
One-off repair: link evidence stored as junk before #94 (#97).

    python scripts/repair_garbled_links.py --list
    python scripts/repair_garbled_links.py --source-id <uuid> --dry-run
    python scripts/repair_garbled_links.py --source-id <uuid> --limit 25 --yes

Dry-run finds the rows without fetching a page or making a model call. A real
run spends money, so it needs --yes and prints an estimate (from this install's
own usage history) first. A row whose page cannot be fetched any more is left
as it was and named in the report.
"""
import argparse
import asyncio
import logging
import sys
import uuid

sys.path.insert(0, "/app" if __file__.startswith("/app") else ".")

from sqlalchemy import func, select

from app.core.database import AsyncSessionLocal
from app.models.source_config import SourceConfig
from app.models.usage_event import UsageEvent
from app.services.link_repair_service import find_garbled_links, repair_garbled_links


async def _garbled_by_source(db) -> list[tuple[uuid.UUID, str, int]]:
    sources = (await db.execute(
        select(SourceConfig.id, SourceConfig.identifier).where(SourceConfig.source_type == "telegram")
    )).all()
    counted = [(sid, ident, len(await find_garbled_links(db, sid))) for sid, ident in sources]
    return [row for row in counted if row[2]]


async def _avg_cost(db, task_type: str) -> float:
    avg = await db.execute(
        select(func.avg(UsageEvent.cost_usd)).where(UsageEvent.task_type == task_type)
    )
    return float(avg.scalar() or 0)


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", help="show garbled counts per source and exit")
    ap.add_argument("--source-id", type=uuid.UUID)
    ap.add_argument("--limit", type=int, help="only the N newest garbled rows")
    ap.add_argument("--dry-run", action="store_true", help="find only — no fetches, no model calls, no cost")
    ap.add_argument("--yes", action="store_true", help="confirm a real (paid) run")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    async with AsyncSessionLocal() as db:
        if args.list or not args.source_id:
            rows = await _garbled_by_source(db)
            if not rows:
                print("No garbled link evidence.")
                return 0
            print(f"{'source_config_id':38} {'identifier':28} {'garbled':>7}")
            for sid, ident, n in rows:
                print(f"{str(sid):38} {(ident or '')[:28]:28} {n:>7}")
            if not args.source_id:
                print("\nPass --source-id to repair one of these.")
            return 0

        if not args.dry_run and not args.yes:
            n = len(await find_garbled_links(db, args.source_id, args.limit))
            relevance = await _avg_cost(db, "relevance_filter")
            distill = await _avg_cost(db, "distillation")
            # Assume every repaired link distills — the honest upper bound.
            print(f"{n} link(s) would be fetched again and judged.")
            print(f"Estimated cost: ~${n * (relevance + distill):.2f} "
                  f"(relevance ~${relevance:.5f} + distillation ~${distill:.5f} per link, upper bound)")
            print("Re-run with --yes to proceed, or --dry-run to find them for free.")
            return 1

        report = await repair_garbled_links(
            db, source_config_id=args.source_id, limit=args.limit, dry_run=args.dry_run
        )
        print(report.summary())
        if report.unfetchable:
            print(f"page no longer fetchable, left as it was: {len(report.unfetchable)}")
            for sid in report.unfetchable[:10]:
                print(f"  {sid}")
            if len(report.unfetchable) > 10:
                print(f"  … and {len(report.unfetchable) - 10} more")
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
