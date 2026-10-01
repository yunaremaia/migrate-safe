"""Tests for migrate-safe detectors."""
import tempfile
from pathlib import Path

from migrate_safe.detectors import (
    Severity,
    analyze_migration,
    detect_add_not_null_no_default,
    detect_alter_column_type_unsafe,
    detect_drop_column_unsafe,
    detect_drop_table_type,
    detect_rename_column,
    has_unsafe_findings,
)
from migrate_safe.parser import (
    MigrationFile,
    parse_sql_file,
)


def _write_sql(content: str) -> Path:
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".sql", delete=False)
    # delete=False keeps the file on disk after the block: parse_sql_file()
    # re-opens it by path, so it must outlive this helper.
    with f:
        f.write(content)
    return Path(f.name)


def _parse(content: str) -> MigrationFile:
    return parse_sql_file(_write_sql(content))


class TestDetectDropColumn:
    def test_drop_column_unsafe(self):
        sql = "ALTER TABLE users DROP COLUMN email;"
        migration = _parse(sql)
        findings = detect_drop_column_unsafe(migration)

        assert len(findings) == 1
        assert findings[0].severity == Severity.UNSAFE
        assert "DROP COLUMN" in findings[0].message
        assert "Two-step" in findings[0].suggestion

    def test_no_drop_column_safe(self):
        sql = "ALTER TABLE users ADD COLUMN email VARCHAR(255);"
        migration = _parse(sql)
        findings = detect_drop_column_unsafe(migration)
        assert len(findings) == 0


class TestDetectRenameColumn:
    def test_rename_column_critical(self):
        sql = "ALTER TABLE users RENAME COLUMN name TO full_name;"
        migration = _parse(sql)
        findings = detect_rename_column(migration)

        assert len(findings) == 1
        assert findings[0].severity == Severity.CRITICAL
        assert "RENAME" in findings[0].message
        assert "Safe alternative" in findings[0].suggestion

    def test_no_rename_safe(self):
        sql = "ALTER TABLE users ADD COLUMN full_name VARCHAR(255);"
        migration = _parse(sql)
        findings = detect_rename_column(migration)
        assert len(findings) == 0


class TestDetectAddNotNullNoDefault:
    def test_add_not_null_no_default_unsafe(self):
        sql = "ALTER TABLE users ADD COLUMN name VARCHAR(255) NOT NULL;"
        migration = _parse(sql)
        findings = detect_add_not_null_no_default(migration)

        assert len(findings) == 1
        assert findings[0].severity == Severity.UNSAFE
        assert "NOT NULL" in findings[0].message

    def test_add_with_default_safe(self):
        sql = "ALTER TABLE users ADD COLUMN name VARCHAR(255) NOT NULL DEFAULT '';"
        migration = _parse(sql)
        findings = detect_add_not_null_no_default(migration)
        assert len(findings) == 0

    def test_add_nullable_safe(self):
        sql = "ALTER TABLE users ADD COLUMN name VARCHAR(255);"
        migration = _parse(sql)
        findings = detect_add_not_null_no_default(migration)
        assert len(findings) == 0


class TestDetectAlterColumnType:
    def test_alter_to_int_warning(self):
        sql = "ALTER TABLE users ALTER COLUMN age TYPE INTEGER;"
        migration = _parse(sql)
        findings = detect_alter_column_type_unsafe(migration)

        assert len(findings) == 1
        assert findings[0].severity == Severity.WARNING
        assert "INTEGER" in findings[0].message

    def test_alter_to_varchar_safe(self):
        sql = "ALTER TABLE users ALTER COLUMN name TYPE VARCHAR(100);"
        migration = _parse(sql)
        findings = detect_alter_column_type_unsafe(migration)
        # VARCHAR is not a risky target type
        assert len(findings) == 0


class TestDetectDropTableType:
    def test_drop_table_warning(self):
        sql = "DROP TABLE old_logs;"
        migration = _parse(sql)
        findings = detect_drop_table_type(migration)

        assert len(findings) == 1
        assert findings[0].severity == Severity.WARNING
        assert "DROP TABLE" in findings[0].message

    def test_drop_type_warning(self):
        sql = "DROP TYPE status_enum;"
        migration = _parse(sql)
        findings = detect_drop_table_type(migration)

        assert len(findings) == 1
        assert findings[0].severity == Severity.WARNING
        assert "DROP TYPE" in findings[0].message


class TestAnalyzeMigration:
    def test_combined_findings(self):
        sql = """
        ALTER TABLE users DROP COLUMN email;
        ALTER TABLE users ADD COLUMN name VARCHAR(255) NOT NULL;
        """
        migration = _parse(sql)
        findings = analyze_migration(migration)

        # Should have DROP_COLUMN (UNSAFE) + ADD_NOT_NULL (UNSAFE)
        assert len(findings) >= 2
        severities = {f.severity for f in findings}
        assert Severity.UNSAFE in severities

    def test_safe_migration(self):
        sql = """
        CREATE TABLE users (
            id SERIAL PRIMARY KEY,
            name VARCHAR(255) NOT NULL DEFAULT ''
        );
        """
        migration = _parse(sql)
        findings = analyze_migration(migration)
        # CREATE TABLE and ADD COLUMN with DEFAULT should be safe
        assert not has_unsafe_findings(findings)


class TestHasUnsafeFindings:
    def test_has_unsafe_true(self):
        sql = "ALTER TABLE users DROP COLUMN email;"
        migration = _parse(sql)
        findings = analyze_migration(migration)
        assert has_unsafe_findings(findings) is True

    def test_has_unsafe_false(self):
        sql = "CREATE TABLE logs (id SERIAL PRIMARY KEY);"
        migration = _parse(sql)
        findings = analyze_migration(migration)
        assert has_unsafe_findings(findings) is False
