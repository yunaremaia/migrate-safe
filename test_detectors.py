"""Tests for migrate-safe detectors."""

from migrate_safe.detectors import detect_unsafe_migration

SAFE_MIGRATION = """
CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT UNIQUE
);

CREATE INDEX idx_users_email ON users(email);
"""

UNSAFE_DROP_COLUMN = """
ALTER TABLE users DROP COLUMN email;
"""

UNSAFE_RENAME_COLUMN = """
ALTER TABLE users RENAME COLUMN email TO email_address;
"""

UNSAFE_ADD_NOT_NULL = """
ALTER TABLE users ADD COLUMN phone VARCHAR(20) NOT NULL;
"""

UNSAFE_ALTER_TYPE = """
ALTER TABLE users ALTER COLUMN id TYPE BIGINT;
"""

UNSAFE_DROP_TABLE = """
DROP TABLE old_logs;
"""


def test_safe_migration():
    findings = detect_unsafe_migration(SAFE_MIGRATION)
    assert len(findings) == 0, f"Expected no findings, got {findings}"


def test_drop_column():
    findings = detect_unsafe_migration(UNSAFE_DROP_COLUMN)
    assert len(findings) == 1
    assert findings[0].code == "MS001"
    assert findings[0].severity == "error"
    assert findings[0].table == "users"
    assert findings[0].column == "email"


def test_rename_column():
    findings = detect_unsafe_migration(UNSAFE_RENAME_COLUMN)
    assert len(findings) == 1
    assert findings[0].code == "MS002"
    assert findings[0].severity == "error"
    assert findings[0].table == "users"
    assert findings[0].column == "email"


def test_add_not_null():
    findings = detect_unsafe_migration(UNSAFE_ADD_NOT_NULL)
    assert len(findings) == 1
    assert findings[0].code == "MS003"
    assert findings[0].severity == "error"
    assert findings[0].table == "users"
    assert findings[0].column == "phone"


def test_alter_type():
    findings = detect_unsafe_migration(UNSAFE_ALTER_TYPE)
    assert len(findings) == 1
    assert findings[0].code == "MS004"
    assert findings[0].severity == "warning"


def test_drop_table():
    findings = detect_unsafe_migration(UNSAFE_DROP_TABLE)
    assert len(findings) == 1
    assert findings[0].code == "MS005"
    assert findings[0].severity == "warning"


def test_add_column_with_default_is_safe():
    """ADD COLUMN NOT NULL WITH DEFAULT is safe — should NOT trigger MS003."""
    sql = "ALTER TABLE users ADD COLUMN status VARCHAR(10) NOT NULL DEFAULT 'active';"
    findings = detect_unsafe_migration(sql)
    ms003 = [f for f in findings if f.code == "MS003"]
    assert len(ms003) == 0, f"ADD COLUMN with DEFAULT should be safe, got {ms003}"


if __name__ == "__main__":
    test_safe_migration()
    test_drop_column()
    test_rename_column()
    test_add_not_null()
    test_alter_type()
    test_drop_table()
    test_add_column_with_default_is_safe()
    print("All tests passed! ✅")
