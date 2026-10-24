"""
Tests for #61 — the standing instructions, and the one way they fail.

A rule written in a prompt lasted one conversation, which is why it is in
`docs/agents/` now. A rule written in a document fails differently: it points at
a script that has been renamed, or it stops being linked from the file agents
actually read, and then it is decoration. Both of those rot silently and neither
shows up in a diff review months later.

The repo's only test runner is pytest, so the guard for the documentation lives
here, next to the locale-parity test that exists for the same reason.
"""
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CLAUDE_MD = REPO_ROOT / "CLAUDE.md"
LIVE_DATA = REPO_ROOT / "docs/agents/live-data.md"

# Any token with a slash in it, anywhere in the document — prose, backticks or a
# fenced command. Backticks alone were not enough: the first version of this
# matched only `like/this`, and the paths most likely to be *run* are the ones
# inside ```bash blocks, which is where a renamed script hides. Proved by
# renaming one and watching this test keep passing.
#
# Narrowed by the filter below to the tokens that are really repo paths:
# `albertofonsecaq/trademind` is a repository, `src/<context>/docs/adr/` is a
# shape, and `/Volumes/Backup/trademind` is somebody else's disk.
_PATH_LIKE = re.compile(r"[A-Za-z0-9_.\-/]*/[A-Za-z0-9_.\-/]*")


def _documents() -> list[Path]:
    """
    Every document whose paths and commands have to still work.

    The backup READMEs are in here deliberately: they are the ones somebody
    reads after losing a disk, which is the worst moment to find out a script
    was renamed, and they were outside this guard until a review pointed it out.
    """
    return [
        CLAUDE_MD,
        *sorted((REPO_ROOT / "docs/agents").glob("*.md")),
        *sorted((REPO_ROOT / "docs/adr").glob("*.md")),
        REPO_ROOT / "backups/README.md",
        REPO_ROOT / "backup-mirror/README.md",
    ]


def _deliberately_absent(references: set[str]) -> set[str]:
    """
    Which of these the repo is deliberately not carrying.

    A gitignored path is one the repo has decided not to contain: `backend/.env`
    is made from the example by whoever clones, `backups/backup.log` appears on
    the first run, `backups/LAST-RUN-FAILED` only when one fails. Their absence
    is the normal state, so the guard must not read it as rot — this test passed
    locally and failed in CI until it knew the difference, because those files
    exist on a machine that has been used and in no fresh clone.

    Asking git is better than a list maintained by hand: the list would go stale
    in exactly the way this test exists to catch, and a renamed script is
    neither present nor ignored, so it still fails.
    """
    if not references:
        return set()
    result = subprocess.run(
        ["git", "check-ignore", "--stdin"],
        cwd=REPO_ROOT, input="\n".join(sorted(references)),
        capture_output=True, text=True, check=False,
    )
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def _referenced_paths(document: Path) -> set[str]:
    top_level = {entry.name for entry in REPO_ROOT.iterdir() if entry.is_dir()}
    found = set()
    for match in _PATH_LIKE.findall(document.read_text()):
        reference = match.strip("`\"'").rstrip(".,;:)")
        if "$" in reference or reference.endswith(("-", "_")):
            # A filename being built by the shell — `backups/trademind-$(date …)`
            # arrives here as the fragment before the substitution.
            continue
        if any(character in reference for character in "<>*") or reference.endswith("/"):
            continue                        # a shape, not a path
        if reference.split("/")[0] not in top_level:
            continue                        # absolute, a URL, or somebody else's
        found.add(reference)
    return found


class TestTheDocsPointAtThingsThatExist:
    @pytest.mark.parametrize("document", _documents(), ids=lambda p: p.name)
    def test_every_path_it_names_is_there(self, document):
        """
        The way a written-down rule dies: it tells you to run a script that was
        renamed, so the redirection is a dead end and production is the only
        database to hand again.
        """
        references = _referenced_paths(document)
        absent = {
            reference for reference in references
            if not (REPO_ROOT / reference).exists()
        }
        missing = sorted(absent - _deliberately_absent(absent))

        assert missing == []


class TestTheRuleIsFindable:
    def test_the_standing_instructions_exist(self):
        assert LIVE_DATA.is_file()

    def test_claude_md_links_them(self):
        """
        `CLAUDE.md` is what an agent reads first. A rule it does not mention is
        a rule nobody arrives at.
        """
        assert "docs/agents/live-data.md" in CLAUDE_MD.read_text()

    def test_claude_md_states_the_rule_and_not_only_the_link(self):
        """
        One sentence of the actual prohibition, so the first file read says what
        the rule is rather than only where it lives.
        """
        text = CLAUDE_MD.read_text().lower()

        assert "never executes anything against it" in text or "does not execute" in text

    def test_the_rule_names_somewhere_else_to_run_it(self):
        """
        A prohibition with nowhere to go is the condition that produced the
        incident, so the alternative is part of the rule rather than a footnote.
        """
        text = LIVE_DATA.read_text()

        assert "scratch_db.py" in text
        assert (REPO_ROOT / "backend/scripts/scratch_db.py").is_file()

    def test_it_requires_a_review_to_say_what_it_ran(self):
        text = LIVE_DATA.read_text().lower()

        assert "says what it ran" in text

    def test_it_records_what_happened(self):
        """
        Without the incident the rule reads as bureaucracy; with it, as obvious.
        """
        text = LIVE_DATA.read_text()

        assert "2026-10-22" in text
        assert "DROP DATABASE" in text
