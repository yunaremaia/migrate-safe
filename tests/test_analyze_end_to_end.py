"""End-to-end tests for analyze_migration.

Ported from the repository-root ``test_detectors.py`` of the initial release,
which exercised the since-removed ``detect_unsafe_migration`` helper. These
tests keep the same scenarios and assertions but drive the current public API
(``analyze_migration`` over a parsed migration), so a full-suite run still
covers every detector through the aggregate entry point.
"""

import tempfile
from pathlib import Path

from migrate_safe.detectors import Severity, analyze_migration
from migrate_safe.parser import parse_sql_file

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

# The detector flags casts that can fail on existing data (VARCHAR/TEXT ->
# INTEGER/BOOLEAN/SERIAL). A widening int -> bigint cast is deliberately not in
# that set, so the risky case used here targets INTEGER.
UNSAFE_ALTER_TYPE = """
ALTER TABLE users ALTER COLUMN name TYPE INTEGER;
"""

UNSAFE_DROP_TABLE = """
DROP TABLE old_logs;
"""


def _findings(sql: str):
    """Parse ``sql`` and return the findings reported for it."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".sql", delete=False) as handle:
        handle.write(sql)
        path = Path(handle.name)
    return analyze_migration(parse_sql_file(path))


def test_safe_migration():
    findings = _findings(SAFE_MIGRATION)
    assert len(findings) == 0, f"Expected no findings, got {findings}"


def test_drop_column():
    findings = _findings(UNSAFE_DROP_COLUMN)
    assert len(findings) == 1
    assert findings[0].severity == Severity.UNSAFE
    assert "DROP COLUMN" in findings[0].message
    assert "`email`" in findings[0].message
    assert "`users`" in findings[0].message


def test_rename_column():
    findings = _findings(UNSAFE_RENAME_COLUMN)
    assert len(findings) == 1
    assert findings[0].severity == Severity.CRITICAL
    assert "RENAME COLUMN" in findings[0].message
    assert "`email`" in findings[0].message
    assert "`users`" in findings[0].message


def test_add_not_null():
    findings = _findings(UNSAFE_ADD_NOT_NULL)
    assert len(findings) == 1
    assert findings[0].severity == Severity.UNSAFE
    assert "ADD COLUMN" in findings[0].message
    assert "NOT NULL" in findings[0].message
    assert "phone" in findings[0].message


def test_alter_type():
    findings = _findings(UNSAFE_ALTER_TYPE)
    assert len(findings) == 1
    assert findings[0].severity == Severity.WARNING
    assert "ALTER COLUMN" in findings[0].message
    assert "INTEGER" in findings[0].message


def test_drop_table():
    findings = _findings(UNSAFE_DROP_TABLE)
    assert len(findings) == 1
    assert findings[0].severity == Severity.WARNING
    assert "DROP TABLE" in findings[0].message
    assert "old_logs" in findings[0].message


def test_add_column_with_default_is_safe():
    """ADD COLUMN NOT NULL WITH DEFAULT is safe — should not be flagged."""
    sql = "ALTER TABLE users ADD COLUMN status VARCHAR(10) NOT NULL DEFAULT 'active';"
    findings = _findings(sql)
    assert findings == [], f"ADD COLUMN with DEFAULT should be safe, got {findings}"
