"""Tests for migrate-safe parser."""
from pathlib import Path
import tempfile
import pytest

from migrate_safe.parser import (
    parse_sql_file,
    parse_migration_directory,
    StatementType,
    SQLStatement,
)


def _write_sql(content: str) -> Path:
    """Write SQL to a temp file and return its path."""
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".sql", delete=False)
    f.write(content)
    f.close()
    return Path(f.name)


class TestParseDropColumn:
    def test_simple_drop_column(self):
        sql = "ALTER TABLE users DROP COLUMN email;"
        path = _write_sql(sql)
        migration = parse_sql_file(path)

        assert len(migration.statements) == 1
        assert migration.statements[0].type == StatementType.DROP_COLUMN
        assert migration.statements[0].column == "email"
        assert migration.statements[0].table == "users"

    def test_drop_column_backtick(self):
        sql = "ALTER TABLE `users` DROP COLUMN `email`;"
        path = _write_sql(sql)
        migration = parse_sql_file(path)

        assert migration.statements[0].type == StatementType.DROP_COLUMN
        assert migration.statements[0].column == "email"


class TestParseAddColumn:
    def test_add_column_not_null_no_default(self):
        sql = "ALTER TABLE users ADD COLUMN name VARCHAR(255) NOT NULL;"
        path = _write_sql(sql)
        migration = parse_sql_file(path)

        stmt = migration.statements[0]
        assert stmt.type == StatementType.ADD_COLUMN
        assert stmt.column == "name"
        assert stmt.details["not_null"] is True
        assert stmt.details["has_default"] is False

    def test_add_column_with_default(self):
        sql = "ALTER TABLE users ADD COLUMN status VARCHAR(50) NOT NULL DEFAULT 'active';"
        path = _write_sql(sql)
        migration = parse_sql_file(path)

        stmt = migration.statements[0]
        assert stmt.type == StatementType.ADD_COLUMN
        assert stmt.details["not_null"] is True
        assert stmt.details["has_default"] is True

    def test_add_column_nullable(self):
        sql = "ALTER TABLE users ADD COLUMN bio TEXT;"
        path = _write_sql(sql)
        migration = parse_sql_file(path)

        stmt = migration.statements[0]
        assert stmt.type == StatementType.ADD_COLUMN
        assert stmt.details["not_null"] is False


class TestParseRenameColumn:
    def test_rename_column(self):
        sql = "ALTER TABLE users RENAME COLUMN name TO full_name;"
        path = _write_sql(sql)
        migration = parse_sql_file(path)

        stmt = migration.statements[0]
        assert stmt.type == StatementType.RENAME_COLUMN
        assert stmt.column == "name"
        assert stmt.details["new_name"] == "full_name"


class TestParseAlterColumnType:
    def test_alter_column_type(self):
        sql = "ALTER TABLE users ALTER COLUMN name TYPE VARCHAR(100);"
        path = _write_sql(sql)
        migration = parse_sql_file(path)

        stmt = migration.statements[0]
        assert stmt.type == StatementType.ALTER_COLUMN_TYPE
        assert stmt.column == "name"
        assert "varchar" in stmt.details["new_type"].lower()


class TestParseDropTable:
    def test_drop_table(self):
        sql = "DROP TABLE old_logs;"
        path = _write_sql(sql)
        migration = parse_sql_file(path)

        stmt = migration.statements[0]
        assert stmt.type == StatementType.DROP_TABLE
        assert stmt.table == "old_logs"

    def test_drop_table_if_exists(self):
        sql = "DROP TABLE IF EXISTS old_logs;"
        path = _write_sql(sql)
        migration = parse_sql_file(path)

        stmt = migration.statements[0]
        assert stmt.type == StatementType.DROP_TABLE


class TestParseDropType:
    def test_drop_type(self):
        sql = "DROP TYPE status_enum;"
        path = _write_sql(sql)
        migration = parse_sql_file(path)

        stmt = migration.statements[0]
        assert stmt.type == StatementType.DROP_TYPE
        assert stmt.table == "status_enum"


class TestParseCreateTable:
    def test_create_table(self):
        sql = """
        CREATE TABLE users (
            id SERIAL PRIMARY KEY,
            name VARCHAR(255) NOT NULL
        );
        """
        path = _write_sql(sql)
        migration = parse_sql_file(path)

        stmt = migration.statements[0]
        assert stmt.type == StatementType.CREATE_TABLE
        assert stmt.table == "users"


class TestMultipleStatements:
    def test_multiple_statements(self):
        sql = """
        ALTER TABLE users ADD COLUMN name VARCHAR(255) NOT NULL DEFAULT '';
        ALTER TABLE users DROP COLUMN old_email;
        """
        path = _write_sql(sql)
        migration = parse_sql_file(path)

        assert len(migration.statements) == 2
        assert migration.statements[0].type == StatementType.ADD_COLUMN
        assert migration.statements[1].type == StatementType.DROP_COLUMN


class TestParseDirectory:
    def test_parse_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            (tmp / "0001_create_users.sql").write_text(
                "CREATE TABLE users (id SERIAL PRIMARY KEY);"
            )
            (tmp / "0002_add_email.sql").write_text(
                "ALTER TABLE users ADD COLUMN email VARCHAR(255) NOT NULL;"
            )
            (tmp / "readme.txt").write_text("not a migration")

            migrations = parse_migration_directory(tmp)
            assert len(migrations) == 2
            assert all(m.path.suffix == ".sql" for m in migrations)
