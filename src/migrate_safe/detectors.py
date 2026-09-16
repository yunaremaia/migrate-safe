"""Safety detectors for SQL migrations.

Implements 5 core detectors for unsafe Drizzle ORM migration patterns:
1. DROP COLUMN without prior SET NULL / DROP DEFAULT (two-step)
2. RENAME COLUMN (breaks old pods immediately)
3. ADD COLUMN NOT NULL without default (fails on existing rows)
4. ALTER COLUMN TYPE with incompatible cast
5. DROP TABLE / DROP TYPE that may still be referenced
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .parser import (
    MigrationFile,
    StatementType,
)


class Severity(Enum):
    SAFE = "safe"
    WARNING = "warning"
    UNSAFE = "unsafe"
    CRITICAL = "critical"


@dataclass
class Finding:
    severity: Severity
    message: str
    migration_file: str
    line_hint: str = ""
    suggestion: str = ""

    def __str__(self) -> str:
        parts = [f"[{self.severity.value.upper()}] {self.message}"]
        if self.line_hint:
            parts.append(f"  Location: {self.line_hint}")
        if self.suggestion:
            parts.append(f"  Suggestion: {self.suggestion}")
        return "\n".join(parts)


def detect_drop_column_unsafe(
    migration: MigrationFile,
    known_columns: set[str] | None = None,
) -> list[Finding]:
    """Detect DROP COLUMN without prior SET NULL or DROP DEFAULT.

    During rolling deployments, old pods still reading the column crash
    when it's dropped. Safe pattern: SET NULL → deploy → DROP COLUMN (next PR).
    """
    findings: list[Finding] = []
    for stmt in migration.statements:
        if stmt.type == StatementType.DROP_COLUMN:
            loc = f"{migration.path}: {stmt.raw[:80]}..."
            findings.append(
                Finding(
                    severity=Severity.UNSAFE,
                    message=(
                        f"DROP COLUMN `{stmt.column}` on `{stmt.table}` — "
                        "unsafe during rolling deployments"
                    ),
                    migration_file=str(migration.path),
                    line_hint=loc,
                    suggestion=(
                        "Two-step migration:\n"
                        "    Step 1: ALTER TABLE ... ALTER COLUMN ... SET NULL (deploy)\n"
                        "    Step 2: ALTER TABLE ... DROP COLUMN ... (next PR, after old pods gone)"
                    ),
                )
            )
    return findings


def detect_rename_column(migration: MigrationFile) -> list[Finding]:
    """Detect RENAME COLUMN — breaks old pods immediately.

    Old pods expect the old column name, new pods expect the new name.
    No safe rolling deployment path exists.
    """
    findings: list[Finding] = []
    for stmt in migration.statements:
        if stmt.type == StatementType.RENAME_COLUMN:
            new_name = stmt.details.get("new_name", "?")
            loc = f"{migration.path}: {stmt.raw[:80]}..."
            findings.append(
                Finding(
                    severity=Severity.CRITICAL,
                    message=(
                        f"RENAME COLUMN `{stmt.column}` → `{new_name}` on `{stmt.table}` — "
                        "immediately breaks rolling deployments"
                    ),
                    migration_file=str(migration.path),
                    line_hint=loc,
                    suggestion=(
                        "Safe alternative:\n"
                        "    Step 1: ADD COLUMN new_name (with compatible type)\n"
                        "    Step 2: Backfill new_name from old_name (deploy)\n"
                        "    Step 3: Update application code to use new_name\n"
                        "    Step 4: DROP COLUMN old_name (next PR)"
                    ),
                )
            )
    return findings


def detect_add_not_null_no_default(migration: MigrationFile) -> list[Finding]:
    """Detect ADD COLUMN with NOT NULL but no DEFAULT.

    Fails on existing rows: new column is NULL but NOT NULL constraint rejects.
    """
    findings: list[Finding] = []
    for stmt in migration.statements:
        if stmt.type == StatementType.ADD_COLUMN:
            details = stmt.details
            if details.get("not_null") and not details.get("has_default"):
                loc = f"{migration.path}: {stmt.raw[:80]}..."
                findings.append(
                    Finding(
                        severity=Severity.UNSAFE,
                        message=(
                            f"ADD COLUMN `{stmt.column}` NOT NULL without DEFAULT on "
                            f"`{stmt.table}` — fails on existing rows"
                        ),
                        migration_file=str(migration.path),
                        line_hint=loc,
                        suggestion=(
                            "Add a DEFAULT value or make nullable:\n"
                            "    Option A: ADD COLUMN ... NOT NULL DEFAULT 'value'\n"
                            "    Option B: ADD COLUMN ... NULL (then backfill + add NOT NULL later)"
                        ),
                    )
                )
    return findings


def detect_alter_column_type_unsafe(migration: MigrationFile) -> list[Finding]:
    """Detect ALTER COLUMN TYPE that may have incompatible cast.

    Some type changes (e.g., VARCHAR→INT, TEXT→BOOLEAN) fail on existing data.
    """
    findings: list[Finding] = []
    risky_targets = {"int", "integer", "boolean", "serial"}

    for stmt in migration.statements:
        if stmt.type == StatementType.ALTER_COLUMN_TYPE:
            new_type = stmt.details.get("new_type", "").lower().split("(")[0]
            if new_type in risky_targets:
                loc = f"{migration.path}: {stmt.raw[:80]}..."
                findings.append(
                    Finding(
                        severity=Severity.WARNING,
                        message=(
                            f"ALTER COLUMN `{stmt.column}` TYPE to {new_type.upper()} on "
                            f"`{stmt.table}` — may fail on existing data"
                        ),
                        migration_file=str(migration.path),
                        line_hint=loc,
                        suggestion=(
                            "Verify all existing values can cast to the new type.\n"
                            "Consider: ADD COLUMN with new type → backfill → DROP old column"
                        ),
                    )
                )
    return findings


def detect_drop_table_type(migration: MigrationFile) -> list[Finding]:
    """Detect DROP TABLE / DROP TYPE that may still be referenced.

    Other tables, views, or application code may reference these.
    """
    findings: list[Finding] = []
    for stmt in migration.statements:
        if stmt.type == StatementType.DROP_TABLE:
            loc = f"{migration.path}: {stmt.raw[:80]}..."
            findings.append(
                Finding(
                    severity=Severity.WARNING,
                    message=f"DROP TABLE `{stmt.table}` — may be referenced by other tables/views",
                    migration_file=str(migration.path),
                    line_hint=loc,
                    suggestion="Verify no foreign keys, views, or application code references this table.",
                )
            )
        elif stmt.type == StatementType.DROP_TYPE:
            loc = f"{migration.path}: {stmt.raw[:80]}..."
            findings.append(
                Finding(
                    severity=Severity.WARNING,
                    message=f"DROP TYPE `{stmt.table}` — may be used by columns or functions",
                    migration_file=str(migration.path),
                    line_hint=loc,
                    suggestion="Verify no columns or functions depend on this type.",
                )
            )
    return findings


def analyze_migration(migration: MigrationFile) -> list[Finding]:
    """Run all safety detectors on a migration file."""
    all_findings: list[Finding] = []
    all_findings.extend(detect_drop_column_unsafe(migration))
    all_findings.extend(detect_rename_column(migration))
    all_findings.extend(detect_add_not_null_no_default(migration))
    all_findings.extend(detect_alter_column_type_unsafe(migration))
    all_findings.extend(detect_drop_table_type(migration))
    return all_findings


def has_unsafe_findings(findings: list[Finding]) -> bool:
    """Check if any findings are UNSAFE or CRITICAL."""
    return any(
        f.severity in (Severity.UNSAFE, Severity.CRITICAL) for f in findings
    )
