# 6. A picture lives in the database, beside its evidence

Date: 2026-10-28

Status: accepted

## Context

Image evidence is read by vision into text, and until now the image itself was
dropped once that was done. The knowledge base could say what a chart a trader
posted showed, but never show it, and understanding a trade means looking at
it (#107, #108).

Keeping the pictures means choosing where they live. The database is backed up
by a dump that is not called a backup until it has been restored and checked
(ADR 0003), and the application's credentials own nothing in it (ADR 0004).
Nothing outside the database is backed up at all. A picture that cannot be
fetched again — the message deleted, the channel gone — is as irreplaceable as
the evidence it belongs to.

## Decision

The picture an image was read from is stored in the database, in a table of its
own, one per piece of evidence, with its media type and the workspace it belongs
to. It is kept for evidence that is on topic or whose verdict could not be read,
and not for evidence clearly judged off topic; when a replay changes the
verdict, what is kept follows it.

A picture is served only to members of its workspace, and only through the API.
Nothing that reads evidence rows reads the picture with them.

## Consequences

- The verified dump backs up the pictures with the evidence they belong to, and
  a restore brings both back together. There is no second store to keep in step.
- Dumps grow by roughly the size of every picture kept, and photos do not
  compress, so the dump, the restore check and every scratch copy get slower by
  the same amount. That cost grows with ingestion and is not bounded.
- Every picture served passes through the application process. That is fine for
  a thumbnail on a page; it would not be for bulk export.
- Workspace isolation is enforced in one place, the same way it is for every
  other row, rather than by a second permission scheme for files.
- A cloned workspace copies its pictures, which doubles their storage.

## Alternatives considered

- **Files on the server's disk, a path in the database.** Smaller dumps and a
  cheap way to serve. Rejected because nothing backs up that disk: the pictures
  would be the one part of the knowledge base a restore does not bring back, and
  a dump would restore rows pointing at files that are not there.
- **Object storage (S3 or similar).** The usual answer at scale, with its own
  durability. Rejected for now because it adds a service, credentials and a
  separate backup and access story to a project that runs on one machine; worth
  revisiting if the dump's size becomes the bottleneck, and this ADR would then
  be superseded.
- **The bytes in the evidence row's metadata, as ingestion already carried
  them.** No new table. Rejected because every query reading evidence would
  carry the pictures with it, and the metadata is served to the browser as is.
- **Not keeping pictures; link to the original message.** Costs nothing.
  Rejected because a link needs the reader to be in that channel, dies with the
  message, and does not exist at all for a channel that has since gone.
