"""CLI for migrate-safe."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import TextIO

import click

from migrate_safe import __version__
from migrate_safe.detectors import Finding, detect_unsafe_migration, filter_findings

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore


def _load_config(config_path: Path | None) -> dict:
    """Load .migratesafe.toml config."""
    if config_path is not None:
        if not config_path.exists():
            click.echo(f"Config not found: {config_path}", err=True)
            sys.exit(2)
        with open(config_path, "rb") as f:
            return tomllib.load(f)

    # Look for default config
    default = Path(".migratesafe.toml")
    if default.exists():
        with open(default, "rb") as f:
            return tomllib.load(f)
    return {}


def _find_migrations(path: Path) -> list[Path]:
    """Find all .sql migration files in a directory."""
    if path.is_file():
        return [path]
    return sorted(path.rglob("*.sql"))


def _format_text(findings: list[Finding], files: list[Path]) -> str:
    """Format findings as human-readable text."""
    if not findings:
        return "✅ No unsafe migration patterns found."

    lines = [f"❌ {len(findings)} unsafe pattern(s) found:\n"]
    for f in findings:
        lines.append(f"  [{f.code}] {f.severity.upper()}")
        lines.append(f"    Table: {f.table}" + (f", Column: {f.column}" if f.column else ""))
        lines.append(f"    {f.message}")
        if f.raw:
            lines.append(f"    SQL: {f.raw}")
        lines.append("")
    return "\n".join(lines)


def _format_sarif(findings: list[Finding], files: list[Path]) -> dict:
    """Format findings as SARIF."""
    return {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "migrate-safe",
                        "version": __version__,
                        "informationUri": "https://github.com/yunaremaia/migrate-safe",
                    }
                },
                "results": [
                    {
                        "ruleId": f.code,
                        "level": "error" if f.severity == "error" else "warning",
                        "message": {"text": f.message},
                        "locations": [
                            {
                                "physicalLocation": {
                                    "region": {
                                        "startLine": 1,
                                    }
                                }
                            }
                        ],
                    }
                    for f in findings
                ],
            }
        ],
    }


@click.command()
@click.argument("path", type=click.Path(exists=True, path_type=Path), default=Path("."))
@click.option("--config", type=click.Path(path_type=Path), help="Path to config file")
@click.option("--format", "fmt", type=click.Choice(["text", "json", "sarif"]), default="text")
@click.option("--output", type=click.File("w"), default="-", help="Output file (default: stdout)")
@click.option("--allow", multiple=True, help="Allow specific codes (e.g., MS005)")
@click.version_option(version=__version__)
def check(path: Path, config, fmt: str, output: TextIO, allow: tuple[str, ...]):
    """Check SQL migrations for unsafe patterns.

    PATH is the directory or file to check (default: current directory).
    """
    cfg = _load_config(config)
    migrate_cfg = cfg.get("migrate-safe", {})

    # Merge allow from config + CLI
    allow_set = set(allow) | set(migrate_cfg.get("allow", []))
    ignore_files = set(migrate_cfg.get("ignore-files", []))

    files = _find_migrations(path)
    all_findings: list[Finding] = []

    for f in files:
        if f.name in ignore_files:
            continue
        sql = f.read_text(encoding="utf-8")
        findings = detect_unsafe_migration(sql)
        findings = filter_findings(findings, allow_set)
        all_findings.extend(findings)

    if fmt == "text":
        output.write(_format_text(all_findings, files) + "\n")
    elif fmt == "json":
        output.write(json.dumps([f.to_dict() for f in all_findings], indent=2) + "\n")
    elif fmt == "sarif":
        output.write(json.dumps(_format_sarif(all_findings, files), indent=2) + "\n")

    if any(f.severity == "error" for f in all_findings):
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    check()
