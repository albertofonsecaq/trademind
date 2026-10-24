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
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CLAUDE_MD = REPO_ROOT / "CLAUDE.md"
LIVE_DATA = REPO_ROOT / "docs/agents/live-data.md"

# A backticked token with a slash in it. Narrowed below to the ones that are
# really repo paths: `albertofonsecaq/trademind` is a repository, and
# `src/<context>/docs/adr/` is a shape rather than a place.
_BACKTICKED = re.compile(r"`([^`\s]+/[^`\s]+)`")


def _documents() -> list[Path]:
    return [
        CLAUDE_MD,
        *sorted((REPO_ROOT / "docs/agents").glob("*.md")),
        *sorted((REPO_ROOT / "docs/adr").glob("*.md")),
    ]


def _referenced_paths(document: Path) -> set[str]:
    top_level = {entry.name for entry in REPO_ROOT.iterdir() if entry.is_dir()}
    found = set()
    for reference in _BACKTICKED.findall(document.read_text()):
        if any(character in reference for character in "<>*") or reference.endswith("/"):
            continue                        # a shape, not a path
        if reference.split("/")[0] not in top_level:
            continue                        # not rooted here at all
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
        missing = sorted(
            reference for reference in _referenced_paths(document)
            if not (REPO_ROOT / reference).exists()
        )

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
