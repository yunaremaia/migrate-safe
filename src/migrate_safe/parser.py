"""SQL migration parser for Drizzle ORM.

Parses .sql migration files and builds a simple AST focused on
ALTER TABLE, DROP, ADD, RENAME patterns.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class StatementType(Enum):
    ALTER_TABLE = "alter_table"
    DROP_TABLE = "drop_table"
    DROP_COLUMN = "drop_column"
    ADD_COLUMN = "add_column"
    RENAME_COLUMN = "rename_column"
    ALTER_COLUMN_TYPE = "alter_column_type"
    DROP_TYPE = "drop_type"
    CREATE_TABLE = "create_table"
    CREATE_INDEX = "create_index"
    OTHER = "other"


@dataclass
class SQLStatement:
    type: StatementType
    table: str = ""
    column: str = ""
    raw: str = ""
    details: dict = field(default_factory=dict)


@dataclass
class MigrationFile:
    path: Path
    statements: list[SQLStatement] = field(default_factory=list)


# Regex patterns for SQL parsing
ALTER_TABLE_RE = re.compile(
    r"ALTER\s+TABLE\s+[`\"']?(\w+)[`\"']?\s+(.*)",
    re.IGNORECASE | re.DOTALL,
)
DROP_COLUMN_RE = re.compile(
    r"DROP\s+COLUMN\s+[`\"']?(\w+)[`\"']?",
    re.IGNORECASE,
)
ADD_COLUMN_RE = re.compile(
    r"ADD\s+COLUMN\s+[`\"']?(\w+)[`\"']?\s+(\w+(?:\([^)]*\))?)\s*(.*)",
    re.IGNORECASE,
)
RENAME_COLUMN_RE = re.compile(
    r"RENAME\s+COLUMN\s+[`\"']?(\w+)[`\"']?\s+TO\s+[`\"']?(\w+)[`\"']?",
    re.IGNORECASE,
)
ALTER_COLUMN_TYPE_RE = re.compile(
    r"(?:ALTER|MODIFY)\s+COLUMN\s+[`\"']?(\w+)[`\"']?\s+(?:SET\s+DATA\s+)?TYPE\s+(\w+(?:\([^)]*\))?)",
    re.IGNORECASE,
)
DROP_TABLE_RE = re.compile(
    r"DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?[`\"']?(\w+)[`\"']?",
    re.IGNORECASE,
)
DROP_TYPE_RE = re.compile(
    r"DROP\s+TYPE\s+(?:IF\s+EXISTS\s+)?[`\"']?(\w+)[`\"']?",
    re.IGNORECASE,
)
NOT_NULL_RE = re.compile(r"NOT\s+NULL", re.IGNORECASE)
DEFAULT_RE = re.compile(r"DEFAULT\s+(\S+)", re.IGNORECASE)
CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"']?(\w+)[`\"']?",
    re.IGNORECASE,
)


def parse_sql_file(path: Path) -> MigrationFile:
    """Parse a SQL migration file into a MigrationFile object."""
    content = path.read_text(encoding="utf-8")
    migration = MigrationFile(path=path)

    # Split by semicolons to get individual statements
    statements = [s.strip() for s in content.split(";") if s.strip()]

    for stmt_text in statements:
        statement = _parse_statement(stmt_text)
        migration.statements.append(statement)

    return migration


def _parse_statement(text: str) -> SQLStatement:
    """Parse a single SQL statement."""
    # Check ALTER TABLE first (most common)
    alter_match = ALTER_TABLE_RE.match(text)
    if alter_match:
        table = alter_match.group(1)
        rest = alter_match.group(2) or ""

        # Determine what kind of ALTER TABLE
        drop_col = DROP_COLUMN_RE.search(rest)
        if drop_col:
            return SQLStatement(
                type=StatementType.DROP_COLUMN,
                table=table,
                column=drop_col.group(1),
                raw=text,
            )

        add_col = ADD_COLUMN_RE.search(rest)
        if add_col:
            col_name = add_col.group(1)
            col_type = add_col.group(2)
            rest_of_def = add_col.group(3) or ""
            is_not_null = bool(NOT_NULL_RE.search(rest_of_def))
            has_default = bool(DEFAULT_RE.search(rest_of_def))
            return SQLStatement(
                type=StatementType.ADD_COLUMN,
                table=table,
                column=col_name,
                raw=text,
                details={
                    "data_type": col_type,
                    "not_null": is_not_null,
                    "has_default": has_default,
                },
            )

        rename_col = RENAME_COLUMN_RE.search(rest)
        if rename_col:
            return SQLStatement(
                type=StatementType.RENAME_COLUMN,
                table=table,
                column=rename_col.group(1),
                raw=text,
                details={"new_name": rename_col.group(2)},
            )

        alter_type = ALTER_COLUMN_TYPE_RE.search(rest)
        if alter_type:
            return SQLStatement(
                type=StatementType.ALTER_COLUMN_TYPE,
                table=table,
                column=alter_type.group(1),
                raw=text,
                details={"new_type": alter_type.group(2)},
            )

        return SQLStatement(
            type=StatementType.ALTER_TABLE,
            table=table,
            raw=text,
        )

    # Check DROP TABLE
    drop_table = DROP_TABLE_RE.match(text)
    if drop_table:
        return SQLStatement(
            type=StatementType.DROP_TABLE,
            table=drop_table.group(1),
            raw=text,
        )

    # Check DROP TYPE
    drop_type = DROP_TYPE_RE.match(text)
    if drop_type:
        return SQLStatement(
            type=StatementType.DROP_TYPE,
            table=drop_type.group(1),
            raw=text,
        )

    # Check CREATE TABLE
    create_table = CREATE_TABLE_RE.match(text)
    if create_table:
        return SQLStatement(
            type=StatementType.CREATE_TABLE,
            table=create_table.group(1),
            raw=text,
        )

    return SQLStatement(type=StatementType.OTHER, raw=text)


def parse_migration_directory(dir_path: Path) -> list[MigrationFile]:
    """Parse all SQL files in a directory."""
    migrations = []
    for sql_file in sorted(dir_path.glob("*.sql")):
        migrations.append(parse_sql_file(sql_file))
    return migrations
