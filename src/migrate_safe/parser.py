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

# Dollar-quoted string opener: $$ ... $$ or $tag$ ... $tag$. These are used by
# PL/pgSQL function bodies, whose contents must survive comment stripping.
_DOLLAR_QUOTE_RE = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*\$|\$\$")


def strip_sql_comments(sql: str) -> str:
    """Remove SQL comments from ``sql``, preserving line structure.

    The classification regexes use ``re.match()``, which anchors at position 0.
    Any statement preceded by a comment therefore failed to match and silently
    degraded to ``StatementType.OTHER`` -- every detector keys off the statement
    type, so a migration full of ``DROP COLUMN`` was reported safe. Stripping
    comments before classification is what makes comment-carrying migrations
    (the norm for Drizzle-generated files) visible to the detectors.

    This is a single left-to-right scanner rather than a regex substitution so
    the lexical cases stay correct:

    * ``--`` inside a string literal, quoted identifier (``"a--b"``,
      ``` `a--b` ```) or dollar-quoted body is data, not a comment.
    * ``#`` is left alone entirely: in PostgreSQL it is the JSON path operator
      (``#>>``), never a comment. MySQL's ``#`` comment form is not supported by
      PostgreSQL/Drizzle and treating it as one would corrupt ``#>>``.
    * A ``--`` appearing mid-line after real SQL starts a comment there, but the
      SQL before it is kept -- so ``ALTER TABLE t DROP COLUMN c -- note`` still
      classifies.
    * Block comments may nest (``/* a /* b */ c */``), as in PostgreSQL.

    Newlines are preserved (a line comment collapses to the newline that ended
    it) so line-oriented diagnostics and blank-line separation survive.
    """
    out: list[str] = []
    i = 0
    length = len(sql)

    while i < length:
        char = sql[i]

        # Single-quoted string literal, with '' and \' escapes.
        if char == "'":
            i = _copy_quoted(sql, i, "'", out)
            continue

        # Quoted identifier: "col--name" or `col--name`.
        if char in ('"', "`"):
            i = _copy_quoted(sql, i, char, out)
            continue

        # Dollar-quoted string: $$ ... $$ or $tag$ ... $tag$.
        if char == "$":
            match = _DOLLAR_QUOTE_RE.match(sql, i)
            if match:
                delimiter = match.group(0)
                end = sql.find(delimiter, match.end())
                end = length if end == -1 else end + len(delimiter)
                out.append(sql[i:end])
                i = end
                continue

        # Line comment: -- to end of line (newline itself is kept).
        if sql.startswith("--", i):
            newline = sql.find("\n", i)
            if newline == -1:
                break
            i = newline
            continue

        # Block comment: /* ... */, nestable.
        if sql.startswith("/*", i):
            i = _skip_block_comment(sql, i)
            out.append(" ")
            continue

        out.append(char)
        i += 1

    return "".join(out)


def _copy_quoted(sql: str, start: int, quote: str, out: list[str]) -> int:
    """Copy a quoted run starting at ``sql[start]`` into ``out``.

    Returns the index just past the closing quote. An unterminated quote runs to
    end of input: unterminated literals are malformed SQL, and keeping the
    remainder is safer than dropping statements we cannot classify.
    """
    length = len(sql)
    out.append(sql[start])
    i = start + 1
    while i < length:
        char = sql[i]
        if char == "\\" and i + 1 < length:
            # Backslash escape (E'...' and standard_conforming_strings=off).
            out.append(sql[i : i + 2])
            i += 2
            continue
        if char == quote:
            if sql.startswith(quote * 2, i):
                # Doubled quote is a literal quote, not a terminator.
                out.append(quote * 2)
                i += 2
                continue
            out.append(quote)
            return i + 1
        out.append(char)
        i += 1
    return length


def _skip_block_comment(sql: str, start: int) -> int:
    """Return the index just past the ``/* ... */`` comment at ``start``."""
    length = len(sql)
    i = start + 2
    depth = 1
    while i < length and depth:
        if sql.startswith("/*", i):
            depth += 1
            i += 2
        elif sql.startswith("*/", i):
            depth -= 1
            i += 2
        else:
            i += 1
    return i


def _split_statements(content: str) -> list[str]:
    """Split SQL text into statements, ignoring semicolons inside comments.

    Splitting on a bare ``";"`` before comment removal let a ``;`` inside a
    comment cut a statement in half, yielding an unclassifiable fragment.
    Comments are dropped first, so only real statement terminators remain.
    """
    stripped = strip_sql_comments(content)
    return [s.strip() for s in stripped.split(";") if s.strip()]


def parse_sql_file(path: Path) -> MigrationFile:
    """Parse a SQL migration file into a MigrationFile object."""
    content = path.read_text(encoding="utf-8")
    migration = MigrationFile(path=path)

    # Split by semicolons to get individual statements (comment-aware)
    statements = _split_statements(content)

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
