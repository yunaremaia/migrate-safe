"""Detectors for unsafe SQL migration patterns."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

__all__ = ["Finding", "detect_unsafe_migration"]

# Pattern matchers — order matters (more specific first)
DROP_COLUMN_RE = re.compile(
    r"ALTER\s+TABLE\s+(?P<table>\w+)\s+DROP\s+COLUMN\s+(?P<column>\w+)",
    re.IGNORECASE,
)
RENAME_COLUMN_RE = re.compile(
    r"ALTER\s+TABLE\s+(?P<table>\w+)\s+RENAME\s+COLUMN\s+(?P<from>\w+)\s+TO\s+(?P<to>\w+)",
    re.IGNORECASE,
)
ADD_NOT_NULL_RE = re.compile(
    r"ALTER\s+TABLE\s+(?P<table>\w+)\s+ADD\s+COLUMN\s+(?P<column>\w+)\s+(?P<type>\w+(?:\(\d+\))?)\s+NOT\s+NULL(?!\s+DEFAULT)",
    re.IGNORECASE,
)
ALTER_TYPE_RE = re.compile(
    r"ALTER\s+TABLE\s+(?P<table>\w+)\s+ALTER\s+COLUMN\s+(?P<column>\w+)\s+TYPE\s+(?P<type>\w+(?:\([^)]*\))?)",
    re.IGNORECASE,
)
DROP_TABLE_RE = re.compile(
    r"DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?P<table>\w+)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str  # "error" | "warning"
    message: str
    table: str
    column: str = ""
    raw: str = ""

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "table": self.table,
            "column": self.column,
            "raw": self.raw.strip(),
        }


def detect_unsafe_migration(sql: str) -> list[Finding]:
    """Detect unsafe patterns in a single migration SQL string."""
    findings: list[Finding] = []
    lines = sql.splitlines()

    for line in lines:
        stripped = line.strip()
        # Skip comments
        if stripped.startswith("--") or stripped.startswith("/*"):
            continue

        if m := DROP_COLUMN_RE.search(line):
            findings.append(Finding(
                code="MS001",
                severity="error",
                message=(
                    f"DROP COLUMN '{m.group('column')}' on table '{m.group('table')}' "
                    "breaks rolling deployments. Old pods still reading this column will crash. "
                    "Use a two-step migration: first SET NULL / DROP DEFAULT, deploy, then DROP COLUMN."
                ),
                table=m.group("table"),
                column=m.group("column"),
                raw=stripped,
            ))

        if m := RENAME_COLUMN_RE.search(line):
            findings.append(Finding(
                code="MS002",
                severity="error",
                message=(
                    f"RENAME COLUMN '{m.group('from')}' → '{m.group('to')}' on table "
                    f"'{m.group('table')}' breaks old pods immediately. "
                    "Add a new column, backfill, then drop the old one in a separate PR."
                ),
                table=m.group("table"),
                column=m.group("from"),
                raw=stripped,
            ))

        if m := ADD_NOT_NULL_RE.search(line):
            findings.append(Finding(
                code="MS003",
                severity="error",
                message=(
                    f"ADD COLUMN '{m.group('column')}' NOT NULL on table '{m.group('table')}' "
                    "fails on existing rows. Add as nullable, backfill, then set NOT NULL in a follow-up."
                ),
                table=m.group("table"),
                column=m.group("column"),
                raw=stripped,
            ))

        if m := ALTER_TYPE_RE.search(line):
            findings.append(Finding(
                code="MS004",
                severity="warning",
                message=(
                    f"ALTER COLUMN '{m.group('column')}' TYPE on table '{m.group('table')}' "
                    "may fail if existing data can't be cast. Consider a cast or USING clause."
                ),
                table=m.group("table"),
                column=m.group("column"),
                raw=stripped,
            ))

        if m := DROP_TABLE_RE.search(line):
            findings.append(Finding(
                code="MS005",
                severity="warning",
                message=(
                    f"DROP TABLE '{m.group('table')}' — verify no other migration or model "
                    "still references this table."
                ),
                table=m.group("table"),
                raw=stripped,
            ))

    return findings


def filter_findings(
    findings: Iterable[Finding],
    allow: set[str] | None = None,
) -> list[Finding]:
    """Filter out allowed codes."""
    allow = allow or set()
    return [f for f in findings if f.code not in allow]
