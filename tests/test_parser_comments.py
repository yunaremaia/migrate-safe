"""Regression tests for SQL comment handling in the migration parser.

Before this fix, ``parse_sql_file()`` handed the raw text of each
semicolon-delimited chunk to the classification regexes. Those regexes use
``re.match()``, which anchors at position 0, so any statement preceded by a
``--`` comment line never matched and silently fell through to
``StatementType.OTHER``. Every detector keys off the statement type, so a
migration full of ``DROP COLUMN`` / ``RENAME COLUMN`` / ``DROP TABLE`` was
reported as safe with exit code 0 -- a false negative on a data-loss detector.

These tests lock in comment-aware classification, including the lexical edge
cases a naive ``lstrip("--")`` gets wrong: ``--`` inside a string literal, ``--``
inside a quoted identifier, ``#`` (a Postgres JSON operator, not a comment), and
semicolons inside comments.

Run against the unfixed parser to see them fail; they should all pass after the
comment-stripping fix.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from click.testing import CliRunner

from migrate_safe.cli import main
from migrate_safe.detectors import Severity, analyze_migration, has_unsafe_findings
from migrate_safe.parser import StatementType, parse_sql_file, strip_sql_comments

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_UNSAFE = REPO_ROOT / "sample_unsafe.sql"
SAMPLE_SAFE = REPO_ROOT / "sample_safe.sql"


def _write_sql(content: str) -> Path:
    """Write SQL to a temp file and return its path.

    ``delete=False`` keeps the file on disk after the block closes:
    ``parse_sql_file()`` re-opens it by path, so it must outlive this helper.
    """
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".sql", delete=False, newline="")
    with f:
        f.write(content)
    return Path(f.name)


def _types(sql: str) -> list[StatementType]:
    return [s.type for s in parse_sql_file(_write_sql(sql)).statements]


def _one_statement(sql: str):
    statements = parse_sql_file(_write_sql(sql)).statements
    assert len(statements) == 1, f"expected exactly one statement, got {statements}"
    return statements[0]


def _findings(sql: str):
    return analyze_migration(parse_sql_file(_write_sql(sql)))


# --------------------------------------------------------------------------
# (a) The repository's own sample_unsafe.sql must be detected as unsafe.
# --------------------------------------------------------------------------


class TestSampleUnsafeFileIsDetected:
    def test_every_statement_in_sample_unsafe_is_classified(self):
        migration = parse_sql_file(SAMPLE_UNSAFE)
        assert [s.type for s in migration.statements] == [
            StatementType.DROP_COLUMN,
            StatementType.RENAME_COLUMN,
            StatementType.ADD_COLUMN,
            StatementType.DROP_TABLE,
            StatementType.ALTER_COLUMN_TYPE,
        ]

    def test_sample_unsafe_columns_and_tables_are_extracted(self):
        migration = parse_sql_file(SAMPLE_UNSAFE)
        drop, rename, _add, drop_table, alter_type = migration.statements

        assert (drop.column, drop.table) == ("email", "users")
        assert (rename.column, rename.table) == ("created_at", "users")
        assert rename.details["new_name"] == "signed_up_at"
        assert (drop_table.table,) == ("old_logs",)
        assert (alter_type.column, alter_type.table) == ("id", "users")
        assert alter_type.details["new_type"].lower() == "bigint"

    def test_sample_unsafe_reports_unsafe_findings(self):
        findings = analyze_migration(parse_sql_file(SAMPLE_UNSAFE))

        assert findings, "sample_unsafe.sql must produce findings"
        assert has_unsafe_findings(findings), "sample_unsafe.sql must be UNSAFE, not WARNING-only"
        assert [f.severity for f in findings].count(Severity.CRITICAL) == 1
        assert [f.severity for f in findings].count(Severity.UNSAFE) == 2

        messages = "\n".join(f.message for f in findings)
        assert "DROP COLUMN" in messages
        assert "RENAME COLUMN" in messages
        assert "DROP TABLE" in messages

    def test_cli_check_sample_unsafe_exits_non_zero(self, capsys=None):
        result = CliRunner().invoke(main, ["check", str(SAMPLE_UNSAFE)])

        assert result.exit_code == 1, result.output
        assert "appear safe" not in result.output
        assert "DROP COLUMN" in result.output
        assert "RENAME COLUMN" in result.output
        assert "DROP TABLE" in result.output

    def test_cli_check_sample_safe_still_exits_zero(self):
        result = CliRunner().invoke(main, ["check", str(SAMPLE_SAFE)])

        assert result.exit_code == 0, result.output
        assert "appear safe" in result.output


# --------------------------------------------------------------------------
# (b) A statement preceded by a single-line `--` comment.
# --------------------------------------------------------------------------


class TestSingleLineComment:
    def test_drop_column_after_one_comment_line(self):
        sql = "-- Step 3: remove the email column\nALTER TABLE users DROP COLUMN email;\n"
        assert _types(sql) == [StatementType.DROP_COLUMN]

    def test_statement_is_first_and_comment_is_trailing(self):
        # The comment sits on the same line, after the statement's keyword.
        stmt = _one_statement("ALTER TABLE users DROP COLUMN email -- legacy column\n;\n")
        assert stmt.type == StatementType.DROP_COLUMN
        assert stmt.column == "email"

    def test_trailing_comment_after_the_semicolon_does_not_leak_into_next_statement(self):
        types = _types(
            "ALTER TABLE users DROP COLUMN email; -- remove email\nDROP TABLE old_logs;\n"
        )
        assert types == [StatementType.DROP_COLUMN, StatementType.DROP_TABLE]

    def test_detector_reports_the_commented_drop_column(self):
        findings = _findings("-- remove email\nALTER TABLE users DROP COLUMN email;\n")
        assert len(findings) == 1
        assert findings[0].severity == Severity.UNSAFE
        assert "`email`" in findings[0].message

    def test_comment_only_file_yields_no_statements(self):
        assert _types("-- nothing to do here\n-- still nothing\n") == []


# --------------------------------------------------------------------------
# (c) Multiple consecutive `--` comment lines.
# --------------------------------------------------------------------------


class TestMultipleConsecutiveComments:
    SQL = (
        "-->\n"
        "-- Migration: 0003_remove_email\n"
        "--\n"
        "--   Step 3: remove the email column\n"
        "--> statement-breakpoint\n"
        "ALTER TABLE users DROP COLUMN email;\n"
    )

    def test_many_leading_comment_lines(self):
        assert _types(self.SQL) == [StatementType.DROP_COLUMN]

    def test_blank_lines_between_comments(self):
        sql = "-- one\n\n\n-- two\n\nALTER TABLE users DROP COLUMN email;\n"
        assert _types(sql) == [StatementType.DROP_COLUMN]

    def test_comment_indented_like_a_header(self):
        sql = "--\n-- Step 1: create users\n--\nDROP TABLE old_logs;\n"
        assert _types(sql) == [StatementType.DROP_TABLE]


# --------------------------------------------------------------------------
# (d) Comment-free input must behave exactly as before (no regression).
# --------------------------------------------------------------------------


class TestCommentFreeStatementsUnaffected:
    @pytest.mark.parametrize(
        ("sql", "expected"),
        [
            ("ALTER TABLE users DROP COLUMN email;", StatementType.DROP_COLUMN),
            ("ALTER TABLE users RENAME COLUMN a TO b;", StatementType.RENAME_COLUMN),
            ("ALTER TABLE users ADD COLUMN bio TEXT;", StatementType.ADD_COLUMN),
            ("ALTER TABLE users ALTER COLUMN id TYPE BIGINT;", StatementType.ALTER_COLUMN_TYPE),
            ("DROP TABLE old_logs;", StatementType.DROP_TABLE),
            ("DROP TABLE IF EXISTS old_logs;", StatementType.DROP_TABLE),
            ("DROP TYPE status_enum;", StatementType.DROP_TYPE),
            ("CREATE TABLE users (id SERIAL PRIMARY KEY);", StatementType.CREATE_TABLE),
            ("SELECT 1;", StatementType.OTHER),
        ],
    )
    def test_comment_free_classification(self, sql, expected):
        assert _types(sql) == [expected]

    def test_strip_helper_is_a_no_op_on_comment_free_sql(self):
        sql = "ALTER TABLE users ADD COLUMN bio VARCHAR(50) NOT NULL DEFAULT 'new';"
        assert strip_sql_comments(sql) == sql

    def test_multi_statement_comment_free_file_still_splits(self):
        sql = (
            "CREATE TABLE users (id SERIAL PRIMARY KEY);\n"
            "ALTER TABLE users DROP COLUMN email;\n"
        )
        assert _types(sql) == [StatementType.CREATE_TABLE, StatementType.DROP_COLUMN]

    def test_comment_free_statement_details_are_unchanged(self):
        stmt = _one_statement("ALTER TABLE users ADD COLUMN phone VARCHAR(20) NOT NULL;")
        assert stmt.type == StatementType.ADD_COLUMN
        assert stmt.details == {"data_type": "VARCHAR(20)", "not_null": True, "has_default": False}

    def test_sample_safe_file_is_unaffected(self):
        # sample_safe.sql holds a CREATE TABLE and a CREATE INDEX. CREATE INDEX
        # has no classification regex in the parser (pre-existing behaviour, not
        # part of this fix), so it lands on OTHER -- exactly as before the fix.
        assert _types(SAMPLE_SAFE.read_text(encoding="utf-8")) == [
            StatementType.CREATE_TABLE,
            StatementType.OTHER,
        ]


# --------------------------------------------------------------------------
# (e) Block comments before and inside a statement.
# --------------------------------------------------------------------------


class TestBlockComments:
    def test_block_comment_before_statement(self):
        sql = "/* Step 3: remove the email column */\nALTER TABLE users DROP COLUMN email;\n"
        assert _types(sql) == [StatementType.DROP_COLUMN]

    def test_multiline_block_comment_before_statement(self):
        sql = (
            "/*\n"
            " * Step 3: remove the email column\n"
            " * Only after old pods are gone.\n"
            " */\n"
            "ALTER TABLE users DROP COLUMN email;\n"
        )
        assert _types(sql) == [StatementType.DROP_COLUMN]

    def test_block_comment_between_keywords(self):
        sql = "ALTER /* legacy table */ TABLE users DROP COLUMN email;\n"
        assert _types(sql) == [StatementType.DROP_COLUMN]

    def test_multiple_block_comments_and_line_comments_mixed(self):
        sql = (
            "-- header\n"
            "/* step 3 */\n"
            "DROP /* no longer needed */ TABLE old_logs;\n"
        )
        assert _types(sql) == [StatementType.DROP_TABLE]

    def test_nested_block_comment(self):
        sql = "/* outer /* inner */ still a comment */\nALTER TABLE users DROP COLUMN email;\n"
        assert _types(sql) == [StatementType.DROP_COLUMN]

    def test_unterminated_block_comment_does_not_raise(self):
        sql = "ALTER TABLE users DROP COLUMN email;\n/* forgot to close this\n"
        assert _types(sql) == [StatementType.DROP_COLUMN]

    def test_block_comment_only_file_yields_no_statements(self):
        assert _types("/* nothing to do */\n") == []


# --------------------------------------------------------------------------
# Semicolons inside comments must not split a statement in half.
# --------------------------------------------------------------------------


class TestSemicolonInsideComment:
    def test_semicolon_in_leading_comment_does_not_split_statement(self):
        sql = (
            "-- Step 3: remove the email column; deploy this only after old pods are gone\n"
            "ALTER TABLE users DROP COLUMN email;\n"
        )
        statements = parse_sql_file(_write_sql(sql)).statements

        assert len(statements) == 1, f"comment semicolon split the statement: {statements}"
        assert statements[0].type == StatementType.DROP_COLUMN
        assert statements[0].column == "email"

    def test_semicolon_in_trailing_comment_does_not_leak(self):
        sql = (
            "ALTER TABLE users RENAME COLUMN a TO b; -- old pods expect `a`; new pods want `b`\n"
            "DROP TABLE old_logs;\n"
        )
        assert _types(sql) == [StatementType.RENAME_COLUMN, StatementType.DROP_TABLE]

    def test_semicolon_inside_a_block_comment_does_not_split(self):
        sql = "/* drop it; it is unused */\nDROP TABLE old_logs;\n"
        assert _types(sql) == [StatementType.DROP_TABLE]


# --------------------------------------------------------------------------
# Lexical edge cases: comment markers that are NOT comments.
# --------------------------------------------------------------------------


class TestCommentMarkersInsideLiterals:
    def test_double_dash_inside_string_literal_is_preserved(self):
        sql = "ALTER TABLE users ADD COLUMN note VARCHAR(20) NOT NULL DEFAULT '--safe';"
        stmt = _one_statement(sql)
        assert stmt.type == StatementType.ADD_COLUMN
        assert stmt.details["has_default"] is True
        assert "'--safe'" in stmt.raw

    def test_doubled_quote_escape_around_a_double_dash(self):
        sql = "ALTER TABLE users ADD COLUMN note VARCHAR(20) NOT NULL DEFAULT 'it''s --fine';"
        stmt = _one_statement(sql)
        assert stmt.type == StatementType.ADD_COLUMN
        assert "'it''s --fine'" in stmt.raw

    def test_escape_string_with_backslash_before_quote(self):
        sql = r"ALTER TABLE users ADD COLUMN note VARCHAR(20) NOT NULL DEFAULT E'it\'s --ok';"
        stmt = _one_statement(sql)
        assert stmt.type == StatementType.ADD_COLUMN
        assert r"--ok" in stmt.raw

    def test_double_dash_inside_quoted_identifier_is_preserved(self):
        # Classification of hyphenated quoted identifiers is a separate,
        # pre-existing limitation of the \w+-based regexes (a plain "us-ers"
        # also lands on OTHER before this fix). What this fix owns is that the
        # `--` inside the quotes is treated as data, not as a comment.
        sql = 'ALTER TABLE "us--ers" DROP COLUMN "co--l";'
        assert strip_sql_comments(sql) == sql
        assert '"us--ers"' in parse_sql_file(_write_sql(sql)).statements[0].raw

    def test_double_dash_inside_backtick_identifier_is_preserved(self):
        sql = "ALTER TABLE `us--ers` DROP COLUMN `co--l`;"
        assert strip_sql_comments(sql) == sql
        assert "`us--ers`" in parse_sql_file(_write_sql(sql)).statements[0].raw

    def test_hash_is_not_a_comment_in_postgres(self):
        # `#>` / `#>>` are JSON path operators, not MySQL line comments.
        sql = "SELECT payload #>> '{a,b}' FROM events;"
        assert strip_sql_comments(sql) == sql

    def test_comment_inside_a_string_does_not_hide_the_following_statement(self):
        sql = (
            "ALTER TABLE users ADD COLUMN note VARCHAR(20) NOT NULL DEFAULT '--x';\n"
            "-- header\n"
            "ALTER TABLE users DROP COLUMN email;\n"
        )
        assert _types(sql) == [StatementType.ADD_COLUMN, StatementType.DROP_COLUMN]

    def test_dollar_quoted_body_keeps_its_comments(self):
        sql = "CREATE FUNCTION f() RETURNS int AS $$ SELECT 1; -- inner\n $$ LANGUAGE sql;"
        assert strip_sql_comments(sql) == sql

    def test_statement_after_a_commented_out_block(self):
        sql = (
            "-- ALTER TABLE users DROP COLUMN email;\n"
            "ALTER TABLE users DROP COLUMN legacy_name;\n"
        )
        statements = parse_sql_file(_write_sql(sql)).statements
        assert len(statements) == 1
        assert statements[0].type == StatementType.DROP_COLUMN
        assert statements[0].column == "legacy_name"


# --------------------------------------------------------------------------
# The helper itself.
# --------------------------------------------------------------------------


class TestStripSqlComments:
    def test_line_comment_removed_up_to_newline(self):
        assert strip_sql_comments("-- gone\nKEEP") == "\nKEEP"

    def test_line_comment_at_end_of_input(self):
        # The separating space before the comment is SQL whitespace, not comment
        # text, so it stays; callers .strip() each statement anyway.
        assert strip_sql_comments("KEEP -- gone").rstrip() == "KEEP"
        assert "gone" not in strip_sql_comments("KEEP -- gone")

    def test_line_count_is_preserved(self):
        stripped = strip_sql_comments("-- one\n-- two\nALTER TABLE t DROP COLUMN c;")
        assert stripped.count("\n") == 2

    def test_crlf_line_endings(self):
        assert strip_sql_comments("-- gone\r\nKEEP").endswith("KEEP")

    def test_is_idempotent(self):
        sql = "-- gone\n/* gone too */ ALTER TABLE t DROP COLUMN c; -- gone"
        once = strip_sql_comments(sql)
        assert strip_sql_comments(once) == once

    def test_empty_and_whitespace_inputs(self):
        assert strip_sql_comments("") == ""
        assert strip_sql_comments("   \n\n") == "   \n\n"
