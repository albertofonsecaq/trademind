#!/usr/bin/env python
"""
Fetch again the pictures of image evidence ingested before they were kept (#111).

    python scripts/recover_pictures.py --list
    python scripts/recover_pictures.py --source-id <uuid> --dry-run
    python scripts/recover_pictures.py --source-id <uuid> --limit 50 --yes

Until #108 an image was read by vision and its picture dropped. This fetches
each such message again from the platform by its id and keeps only the
picture: no model is called, so it costs nothing but the fetch, and the
evidence's text, verdict and trade ideas are left as they are.

--list and --dry-run only count, with no platform call. A real run writes to
the database it is pointed at, so it needs --yes: take a verified backup
first, and rehearse it on a copy (scripts/scratch_db.py). --limit takes the
newest first, so a partial run recovers the most useful pictures. Running it
again only fetches what is still missing.
"""
import argparse
import asyncio
import logging
import sys
import uuid

sys.path.insert(0, "/app" if __file__.startswith("/app") else ".")

from app.core.database import AsyncSessionLocal
from app.services.picture_recovery import missing_by_source, recover_pictures


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", help="show, per source, how many pictures are missing")
    ap.add_argument("--source-id", type=uuid.UUID)
    ap.add_argument("--limit", type=int, help="only the N newest")
    ap.add_argument("--dry-run", action="store_true", help="count only — no platform call, no write")
    ap.add_argument("--yes", action="store_true", help="confirm a real run, which writes to the database")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    async with AsyncSessionLocal() as db:
        if args.list or not args.source_id:
            rows = await missing_by_source(db)
            if not rows:
                print("No image evidence is missing its picture.")
                return 0
            print(f"{'source_config_id':38} {'identifier':28} {'missing':>7}  range")
            for sid, ident, n, lo, hi in rows:
                span = f"{lo:%Y-%m-%d} → {hi:%Y-%m-%d}" if lo and hi else "unknown"
                print(f"{str(sid):38} {(ident or '')[:28]:28} {n:>7}  {span}")
            if not args.source_id:
                print("\nPass --source-id to recover one of these.")
            return 0

        if not args.dry_run and not args.yes:
            report = await recover_pictures(db, source_config_id=args.source_id, limit=args.limit, dry_run=True)
            print(f"{report.without_picture} picture(s) would be fetched again from the platform and kept.")
            print("No model is called. This writes to the database this script is pointed at:")
            print("take a verified backup first, then re-run with --yes.")
            return 1

        report = await recover_pictures(
            db, source_config_id=args.source_id, limit=args.limit, dry_run=args.dry_run
        )
        print(report.summary())
        if report.missing:
            print(f"missing (deleted at the source, or no photo any more): {len(report.missing)}")
            for sid in report.missing[:10]:
                print(f"  {sid}")
            if len(report.missing) > 10:
                print(f"  … and {len(report.missing) - 10} more")
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
