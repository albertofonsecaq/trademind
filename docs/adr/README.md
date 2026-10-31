# Architecture decision records

One file per decision, numbered in the order they were taken:
`NNNN-short-title-in-the-present-tense.md`.

Each one says what was decided, not what was done. The sections are:

- **Context** — the forces that made a decision necessary, including whatever is
  true and undocumented today. If the decision exists because something bit us,
  say what bit us.
- **Decision** — stated in the present tense, as a rule a reader can apply.
- **Consequences** — what follows, including the parts that are worse. A
  decision with no costs listed has not been thought about.
- **Alternatives considered** — the real options, each rejected on its merits.
  A strawman here makes the record worth less than no record.

`Status` is one of `accepted`, `accepted, amended by NNNN`, `superseded by NNNN`
or `withdrawn`. An ADR is not edited to reflect a change of mind: a new one
supersedes it, and the old one keeps its reasoning so the change is legible.
When a new one changes only part of an old one, the old one's status says so and
nothing else in it changes, so a reader of the old one knows to read on.

Avoid naming files, functions or line numbers. Those go stale and the decision
does not. `docs/agents/domain.md` tells agents to read these before working in
an area, and to flag a conflict rather than quietly overriding one.
