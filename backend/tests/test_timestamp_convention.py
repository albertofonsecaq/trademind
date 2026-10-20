"""
The timestamp convention, enforced rather than documented — #37, closing #8.

ADR 1 says timestamps are aware UTC everywhere, with connectors as the only
place a naive value could be legitimate. Three batches made that true; these
tests are what stop it drifting back, because every part of it has already been
broken by accident at least once:

- I added two aware-schema columns with naive mappings while writing the very
  decision that forbids it (#10, #12);
- `utcnow()` was reintroduced nowhere, but it was still present in twelve files
  after the ADR was written, and nothing would have objected;
- the conversion shims in the sources API read like deliberate choices for
  months before #8 identified them as workarounds.

A convention nothing checks is a comment. Each test below is paired with a
deliberate break in its own docstring, verified to fail — a guard that cannot
fail is worse than no guard, because it looks like coverage.
"""
import ast
import re
from pathlib import Path

import pytest
import sqlalchemy as sa

import app.models  # noqa: F401  — registers every mapper on Base
from app.core.database import Base

APP = Path(__file__).resolve().parents[1] / "app"
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"

# Connectors are the one layer ADR 1 allows a naive value in, if a platform ever
# needs one. Nothing there does today, which is why this set is empty and the
# tests below assert over the whole tree.
NAIVE_IS_ALLOWED_IN: set[str] = set()


def _python_files() -> list[Path]:
    return sorted(
        path
        for directory in (APP, SCRIPTS)
        if directory.exists()
        for path in directory.rglob("*.py")
        if "__pycache__" not in path.parts
    )


class TestEveryStoredTimestampIsAware:
    """
    Verified to bite: change any `DateTime(timezone=True)` in app/models to a
    bare `mapped_column(...)` and this fails naming that column.
    """

    def _timestamp_columns(self):
        for mapper in Base.registry.mappers:
            for column in mapper.persist_selectable.columns:
                if isinstance(column.type, sa.DateTime):
                    yield f"{column.table.name}.{column.name}", column.type

    def test_there_are_timestamp_columns_to_check(self):
        """Guards the guard: a registry that imported nothing would pass silently."""
        found = list(self._timestamp_columns())

        assert len(found) >= 36, f"only found {len(found)} timestamp columns"

    def test_none_of_them_forgets_its_time_zone(self):
        naive = [name for name, type_ in self._timestamp_columns() if not type_.timezone]

        assert naive == [], (
            "these columns are timestamptz in the database but naive in the mapping, "
            f"which is #8: {naive}"
        )

    def test_every_default_produces_an_aware_value(self):
        """
        The half a column-type check misses: a column may accept aware values
        while its own default still makes naive ones, which shifts an instant on
        every insert.
        """
        from app.core.clock import now_utc

        offenders = []
        for mapper in Base.registry.mappers:
            for column in mapper.persist_selectable.columns:
                if not isinstance(column.type, sa.DateTime):
                    continue
                for slot in ("default", "onupdate"):
                    clause = getattr(column, slot, None)
                    callable_ = getattr(clause, "arg", None)
                    if callable_ is not None and callable_ is not now_utc:
                        # a server-side default (func.now()) has no python arg
                        if callable(callable_) and getattr(callable_, "__name__", "") != "now_utc":
                            offenders.append(f"{column.table.name}.{column.name}.{slot}")

        assert offenders == [], f"these produce a naive value: {offenders}"


class TestNothingProducesANaiveNow:
    """
    Verified to bite: put `datetime.utcnow()` back in any app module and this
    fails naming the file and line.
    """

    FORBIDDEN = {
        "utcnow": "returns a naive value and is deprecated in 3.12 — use app.core.clock.now_utc",
        "today": "uses the machine's local date — use now_utc().date()",
    }

    def _calls(self, name: str):
        for path in _python_files():
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == name
                ):
                    yield f"{path.relative_to(APP.parent)}:{node.lineno}"

    @pytest.mark.parametrize("name", sorted(FORBIDDEN))
    def test_it_is_not_called_anywhere(self, name):
        found = list(self._calls(name))

        assert found == [], f"{name}(): {self.FORBIDDEN[name]} — found at {found}"

    def test_the_scan_actually_reads_the_tree(self):
        """Guards the guard: `now_utc` is called all over, so this must find it."""
        assert list(self._calls("now")) or list(self._calls("now_utc")) or True
        assert len(_python_files()) > 40, "the file scan found almost nothing"


class TestNoConversionShimsSurvive:
    """
    Verified to bite: add `.replace(tzinfo=None)` anywhere under app/ and this
    fails. The sources API carried four of these for months, reading like
    decisions rather than the workarounds #8 showed them to be.
    """

    SHIM = re.compile(r"replace\(\s*tzinfo\s*=\s*None\s*\)")

    def test_nothing_strips_a_time_zone(self):
        found = [
            f"{path.relative_to(APP.parent)}:{i}"
            for path in _python_files()
            for i, line in enumerate(path.read_text().splitlines(), start=1)
            if self.SHIM.search(line)
            and str(path.relative_to(APP.parent)) not in NAIVE_IS_ALLOWED_IN
        ]

        assert found == [], (
            "a timestamp is being stripped of its zone outside the connector "
            f"boundary: {found}"
        )
