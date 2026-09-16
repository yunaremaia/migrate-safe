"""Tests for migrate-safe CLI."""
from click.testing import CliRunner
import tempfile
import pytest
from pathlib import Path

from migrate_safe.cli import main


@pytest.fixture
def runner():
    return CliRunner()


class TestCliCheck:
    def test_check_safe_file(self, runner):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".sql", delete=False) as f:
            f.write("CREATE TABLE users (id SERIAL PRIMARY KEY);")
            f.flush()

            result = runner.invoke(main, ["check", f.name])
            assert result.exit_code == 0
            assert "safe" in result.output.lower() or "✓" in result.output

    def test_check_unsafe_file(self, runner):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".sql", delete=False) as f:
            f.write("ALTER TABLE users DROP COLUMN email;")
            f.flush()

            result = runner.invoke(main, ["check", f.name])
            assert result.exit_code == 1
            assert "DROP COLUMN" in result.output

    def test_check_directory(self, runner):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            (tmp / "0001_safe.sql").write_text("CREATE TABLE users (id SERIAL PRIMARY KEY);")
            (tmp / "0002_unsafe.sql").write_text(
                "ALTER TABLE users DROP COLUMN email;"
            )

            result = runner.invoke(main, ["check", str(tmp)])
            assert result.exit_code == 1

    def test_check_empty_dir(self, runner):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = runner.invoke(main, ["check", tmpdir])
            assert result.exit_code == 0
            assert "No .sql" in result.output

    def test_check_nonexistent(self, runner):
        result = runner.invoke(main, ["check", "/nonexistent/path"])
        assert result.exit_code != 0


class TestCliExplain:
    def test_explain_safe(self, runner):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".sql", delete=False) as f:
            f.write("CREATE TABLE users (id SERIAL PRIMARY KEY);")
            f.flush()

            result = runner.invoke(main, ["explain", f.name])
            assert result.exit_code == 0
            assert "✓" in result.output or "no issues" in result.output

    def test_explain_unsafe(self, runner):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".sql", delete=False) as f:
            f.write("ALTER TABLE users DROP COLUMN email;")
            f.flush()

            result = runner.invoke(main, ["explain", f.name])
            assert result.exit_code == 0
            assert "DROP COLUMN" in result.output


class TestCliStrict:
    def test_strict_warning_fails(self, runner):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".sql", delete=False) as f:
            f.write("DROP TYPE status_enum;")
            f.flush()

            result = runner.invoke(main, ["check", "--strict", f.name])
            assert result.exit_code == 1
