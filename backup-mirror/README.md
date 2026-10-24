# Backup mirror

A second copy of every verified dump, kept apart from `backups/`.

**The dumps in here are gitignored.** Only this file is tracked.

## What this protects against, and what it does not

It protects against `backups/` being lost on its own: deleted by accident, a
retention rule with a bug in it, a directory somebody cleaned up. Each dump here
was verified by restore before it was copied, so what is in here is known to
restore.

**It is on the same disk as `backups/`.** It is not protection against losing
this machine — if the disk fails, both copies go with it, and so does the
database they are copies of. #51's last criterion asks for a copy whose survival
does not depend on this machine, and this is not that copy.

Pointing `BACKUP_MIRROR_DIR` at an external volume or a folder a sync client
watches is what closes that, and the one command below is where to change it.

## How a copy gets here

`backend/scripts/backup.py` copies each dump here after verifying it, when
`BACKUP_MIRROR_DIR` points at this folder:

```bash
BACKUP_MIRROR_DIR="$PWD/backup-mirror" python backend/scripts/backup.py
```

The scheduled run carries the setting in its launchd agent:

```bash
backend/scripts/install-backup-schedule.sh --mirror "$PWD/backup-mirror"
```

Retention here is the same rule as `backups/`: the newest dump of each of the
last 7 days, plus the newest of each of the last 4 ISO weeks. A copy is written
under a temporary name and moved into place, so an interrupted copy never looks
like a complete dump — which matters most in the directory you would reach for
after losing the other one.
