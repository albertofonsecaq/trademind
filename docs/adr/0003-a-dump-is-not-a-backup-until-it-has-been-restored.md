# 3. A dump is not a backup until it has been restored, and a hand-taken one is never pruned

Date: 2026-10-21

Status: accepted

## Context

This database is months of paid ingestion — evidence that cost relevance checks,
images that cost vision calls, transcripts that cost Whisper — and some of the
history behind it is no longer fetchable from the platforms it came from. It
cannot be rebuilt by re-running anything, at any price.

For a long time its only protection was dumps somebody remembered to take. Those
were each restored into a throwaway database and counted before being trusted,
because the way a dump betrays you is not by being absent: `pg_dump` exits 0,
the file is the expected size, and it restores into an empty schema. Nothing
about the file says which of those happened.

Automating that procedure raises two questions that are easy to get wrong in the
direction of losing data, and both of them look like details until the day they
are not.

## Decision

**A dump counts as a backup only after it has been restored and counted.** A
dump that fails that is kept, under a name that cannot be mistaken for a good
one, and the failure is made visible outside the log.

Verification compares the restored copy against the live database, but never
demands equality: the dump is a snapshot and ingestion continues, so equal
counts are not something an honest passing run can promise. It requires that
every expected table is present, that the tables whose emptiness would be
catastrophic are not empty, and that nothing has more rows than the live
database — which would mean the dump is of something else.

**A dump taken by hand is never deleted by retention.** Automatic pruning
considers only files the scheduled job wrote, identified by their own naming.
Age is not a reason to delete a snapshot somebody took deliberately, because the
reason they took it was usually that they were about to do something risky.

**Retention counts days and weeks, not files.** Keeping "the newest 7 files"
reads the same as "a week of history" only while exactly one dump is taken per
day; the job can be run by hand, and an afternoon of manual runs under that rule
deletes the week it was meant to protect.

## Consequences

Every scheduled run pays for a restore of the whole database, which is the cost
of knowing the dump works. At present that is around five seconds on top of a
six-second dump, and it grows with the data.

Verification needs somewhere to restore *to*, so the schedule depends on a
reachable database server, not just a filesystem. It creates and drops its own
throwaway database to do it, and that is the one irreversible command in the
whole arrangement — so it refuses to drop any name without the throwaway prefix.

The tolerance in the comparison is a real gap: a dump missing rows the live
database gained *before* the dump started would still pass, because the check
cannot tell that from ordinary ingestion. Closing it would need the snapshot's
own transaction boundary, which is more machinery than the risk warrants today.

A hand-taken dump accumulates for ever by design, and nothing warns about the
disk it occupies. That is accepted: a forgotten 120 MB file is a far smaller
problem than a retention rule that deleted the one snapshot somebody wanted.

## Alternatives considered

**Trust the exit code.** It is what most backup scripts do, and it is why most
people discover their backups do not work during a restore. The specific failure
it misses — a clean restore into an empty schema — was reproduced deliberately
and is now a test.

**Require the restored counts to equal the live ones.** Stricter, and wrong: the
poller ingests while the verification runs, so this fails whenever the system is
working. An alarm that goes off on healthy days is one nobody reads, which costs
the next real failure.

**Prune by age alone — "delete anything older than 30 days".** Simple, and it
would eventually delete the pre-migration snapshots that exist precisely because
somebody was about to change how stored data is read. Distinguishing the two
kinds of dump is the whole point.

**Let the application's scheduler take backups.** It is already running and it
already has a database session. It also stops when the application stops, which
is one of the moments a backup matters most.
