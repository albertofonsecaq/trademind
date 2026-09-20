#!/usr/bin/env python
"""
One-off repair: replay the images whose vision reply was unreadable (#3).

    python scripts/reprocess_flagged_images.py --list
    python scripts/reprocess_flagged_images.py --source-id <uuid> --dry-run
    python scripts/reprocess_flagged_images.py --source-id <uuid> --limit 25 --yes

Dry-run scans the source without a single model call. A real run spends money,
so it needs --yes and prints an estimate (from this install's own usage history)
first. --limit takes the newest flagged items, so a partial run recovers the
most useful evidence.
"""
import argparse
import asyncio
import logging
import sys
import uuid

sys.path.insert(0, "/app" if __file__.startswith("/app") else ".")

from sqlalchemy import select, func

from app.core.database import AsyncSessionLocal
from app.models.evidence_item import EvidenceItem
from app.models.source_config import SourceConfig
from app.models.usage_event import UsageEvent
from app.services.reprocess_service import reprocess_flagged_images


async def _flagged_by_source(db):
    rows = await db.execute(
        select(
            EvidenceItem.source_config_id,
            SourceConfig.identifier,
            func.count().label("n"),
            func.min(EvidenceItem.message_timestamp),
            func.max(EvidenceItem.message_timestamp),
        )
        .join(SourceConfig, SourceConfig.id == EvidenceItem.source_config_id)
        .where(EvidenceItem.needs_reprocessing.is_(True), EvidenceItem.type == "image")
        .group_by(EvidenceItem.source_config_id, SourceConfig.identifier)
        .order_by(func.count().desc())
    )
    return rows.all()


async def _avg_cost(db, task_type: str) -> float:
    avg = await db.execute(
        select(func.avg(UsageEvent.cost_usd)).where(UsageEvent.task_type == task_type)
    )
    return float(avg.scalar() or 0)


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", help="show flagged counts per source and exit")
    ap.add_argument("--source-id", type=uuid.UUID)
    ap.add_argument("--limit", type=int, help="only the N newest flagged items")
    ap.add_argument("--dry-run", action="store_true", help="scan only — no model calls, no cost")
    ap.add_argument("--yes", action="store_true", help="confirm a real (paid) run")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    async with AsyncSessionLocal() as db:
        if args.list or not args.source_id:
            rows = await _flagged_by_source(db)
            if not rows:
                print("Nothing flagged for reprocessing.")
                return 0
            print(f"{'source_config_id':38} {'identifier':28} {'flagged':>7}  range")
            for sid, ident, n, lo, hi in rows:
                span = f"{lo:%Y-%m-%d} → {hi:%Y-%m-%d}" if lo and hi else "unknown"
                print(f"{str(sid):38} {(ident or '')[:28]:28} {n:>7}  {span}")
            if not args.source_id:
                print("\nPass --source-id to reprocess one of these.")
            return 0

        if not args.dry_run and not args.yes:
            n = next((r[2] for r in await _flagged_by_source(db) if r[0] == args.source_id), 0)
            n = min(n, args.limit) if args.limit else n
            vision = await _avg_cost(db, "vision")
            distill = await _avg_cost(db, "distillation")
            # Assume every recovered item distills — the honest upper bound.
            print(f"{n} item(s) would be reprocessed.")
            print(f"Estimated cost: ~${n * (vision + distill):.2f} "
                  f"(vision ~${vision:.5f} + distillation ~${distill:.5f} per item, upper bound)")
            print("Re-run with --yes to proceed, or --dry-run to scan for free.")
            return 1

        report = await reprocess_flagged_images(
            db, source_config_id=args.source_id, limit=args.limit, dry_run=args.dry_run
        )
        print(report.summary())
        if report.unreachable:
            print(f"unreachable (not found in the scanned range): {len(report.unreachable)}")
            for sid in report.unreachable[:10]:
                print(f"  {sid}")
            if len(report.unreachable) > 10:
                print(f"  … and {len(report.unreachable) - 10} more")
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
