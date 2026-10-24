# 4. The application owns nothing in its own database

Date: 2026-10-24

Status: accepted

## Context

On 2026-10-22 this database was dropped by a tool that held the application's
own credentials. It was recovered from a dump taken five minutes earlier and
nothing was lost, but nothing about the database had stood in the way: one role
owned the database and every object in it, that role was in every connection
string in the repository, and the only check between any process and the whole
knowledge base was an `if` statement inside a Python helper. An `if` protects
against a mistake. It does not protect against anything that edits the file.

What is behind that credential is months of paid ingestion — evidence that cost
relevance checks, images that cost vision calls, transcripts that cost Whisper —
and some of the source history is no longer fetchable at any price.

The jobs that touch this database are not alike. Migrations change its shape.
The application reads and writes rows and has never needed to change its shape.
The backup reads everything and needs a database of its own to restore into, to
prove a dump is real. Those were one role because one role was easy.

## Decision

Three roles, by job, and the application's owns nothing.

The **owner** owns the database and every object in it, and only `alembic
upgrade` connects as it. The **application** role may select, insert, update and
delete, has no ownership, no `CREATE` on the schema, and cannot create
databases. The **backup** role reads every table through `pg_read_all_data` and
may create databases, so it can dump and verify by restore — and cannot drop the
live one.

Default privileges are granted on the owner's behalf, so a table a later
migration adds is readable by the application without a second step.

The roles are produced by a setup step that is safe to run repeatedly, not by a
migration: roles live in the cluster rather than the database, and the owner has
to exist before the migrations that run as it.

## Consequences

The path that was actually taken is closed. Nothing the application, the
migrations or the scripts carry can drop a table or a database, whatever happens
to a guard in the code.

A role without superuser cannot replay everything a dump contains. Verifying a
backup now restores with ownership, grants and comments skipped, because
replaying any of the three needs privileges the backup role deliberately lacks —
and none of them bear on whether the rows are all there. For the same reason the
throwaway database is copied from a template that already has pgvector in it:
`CREATE EXTENSION` is a superuser's privilege, and a dump of this database asks
for it.

Restoring into a fresh database now has a second step. The restored copy is
owned by whoever restored it and has no grants, so the setup step has to run
against it before the application can use it. That is written into the restore
procedure, where somebody under pressure will read it.

**This does not make the database safe from everyone.** The bootstrap superuser
the container creates still exists and still trusts local socket connections, so
anyone with shell and Docker access can still drop everything. Removing that
needs a second superuser to exist first, or a mistake leaves the cluster with no
way back. What remains is covered by the off-machine copy and by the standing
instruction that agents do not execute against the live database.

## Alternatives considered

**Keep one role and rely on the guard in the script.** That was the arrangement
on 2026-10-22. The guard was correct and was bypassed by editing it out; code
that protects data has to assume the code can be changed.

**Demote the bootstrap superuser in the same change.** It closes the remaining
path, and it is how a cluster ends up with no superuser at all if the second one
is forgotten — a failure with no recovery short of single-user mode. It is worth
doing deliberately, with a recovery role in place, and not as a side effect of
this.

**Put the roles in a migration.** Appealing because migrations are versioned and
reviewed. Roles are cluster-wide rather than per-database, the migrations run as
the owner so the owner cannot be created by one, and `alembic downgrade` would
have to decide whether to delete roles that other databases might be using.

**Give the backup role superuser so a restore replays everything.** It makes
verification simpler and gives the credential that runs unattended every night
the power to destroy what it is protecting.
