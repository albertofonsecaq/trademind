# 2. A platform connection has three states, and a rejected session is one of them

Date: 2026-10-20

Status: accepted

## Context

A platform connection is either waiting to be signed in, usable, or not usable.
Fetching asks one question of it — may this source fetch right now — and one
gate answers that for every path: the recurring poll, the on-demand endpoints,
and the background runners that re-read after work was queued.

Credentials stop working in more than one way. The user can revoke a connection
deliberately. Telegram can also end the session from its own side: the user
terminates that device, the session expires, two-factor is re-armed, the account
is deactivated, the key is used from two addresses at once. Until recently none
of those were written down anywhere — the state said usable, the work was
accepted, and the fetch failed in the background with a log line.

The obvious reading of that is that each cause deserves its own state, so the
stored value says what happened.

## Decision

A connection's status is one of `pending`, `active` and `revoked`, and every
cause of unusable credentials is recorded as `revoked`.

Why the connection cannot fetch belongs on whatever is reporting the failure —
the job row that stopped, the log line, the message shown to the user — not in
the status value. A new status is justified only by a path through the product
that has to branch on it, never by a cause being distinguishable.

## Consequences

One comparison decides whether a source may fetch, and it keeps working when the
next cause of a dead session arrives. A cause nobody has seen yet still turns
the source off instead of being treated as healthy, which is the failure mode
this is chosen against.

The user sees "not usable, reconnect it" whether they ended the session or
Telegram did. That is the cost, and it is real: the one case that happens while
they are not looking reads exactly like the one they did themselves, so the row
cannot tell them "this stopped working on its own". The row has never claimed
otherwise, and the backfill that stopped says what happened, but somebody will
eventually want the difference on the sources page. The answer then is a
recorded reason beside the status, not a fourth status.

Status also cannot say *when* usability was lost, or how often a connection has
been rejected and reconnected — questions nobody has asked yet.

## Alternatives considered

**A status per cause (`expired`, `terminated`, `deactivated`, `rejected`).**
Honest about the cause and useless at the point of use: every one of them means
"cannot fetch", so the gate becomes a list that must be kept complete, and the
cause Telegram adds next year is missing from it. Missing means absent from the
list of unusable states, which reads as usable — the gate would offer work on
credentials known to be dead. Each new state also needs its own badge tone and
its own two translations to say what `revoked` already says.

**A boolean beside the status (`credentials_ok`).** Two fields that can disagree,
and no answer for which one wins. The question "may this fetch" would be asked
of both, and would eventually be asked of only one.

**A reason column recorded beside the status.** Not rejected on its merits —
rejected as premature. It is the right answer to a question nobody has asked
yet, it costs a migration against a database of paid ingestion, and it is what
should be added the day the sources page needs to distinguish the causes. The
status decision above does not change when it is.
