"""CLI for migrate-safe."""
from __future__ import annotations

import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from . import __version__
from .detectors import Severity, analyze_migration, has_unsafe_findings
from .parser import parse_migration_directory, parse_sql_file

console = Console()


@click.group()
@click.version_option(version=__version__)
def main() -> None:
    """migrate-safe: Migration safety linter for Drizzle ORM.

    Detects unsafe SQL migration patterns that can break rolling deployments.
    """


@main.command()
@click.argument("path", type=click.Path(exists=True))
@click.option(
    "--strict",
    is_flag=True,
    help="Exit with error code on WARNING findings (not just UNSAFE/CRITICAL).",
)
def check(path: str, strict: bool) -> None:
    """Check a migration file or directory for safety issues."""
    target = Path(path)

    if target.is_file():
        migrations = [parse_sql_file(target)]
    elif target.is_dir():
        migrations = parse_migration_directory(target)
    else:
        console.print(f"[red]Error:[/red] {path} is not a file or directory")
        sys.exit(1)

    if not migrations:
        console.print("[yellow]No .sql migration files found.[/yellow]")
        sys.exit(0)

    all_findings = []
    total_files = 0
    unsafe_files = 0

    for migration in migrations:
        total_files += 1
        findings = analyze_migration(migration)
        if findings:
            all_findings.extend(findings)
            if has_unsafe_findings(findings):
                unsafe_files += 1

    # Report
    if not all_findings:
        console.print(
            f"[bold green]✓ All {total_files} migration(s) appear safe.[/bold green]"
        )
        sys.exit(0)

    # Summary table
    table = Table(title="migrate-safe findings")
    table.add_column("Severity", style="bold")
    table.add_column("Message")
    table.add_column("Location")

    for finding in all_findings:
        style = {
            Severity.SAFE: "green",
            Severity.WARNING: "yellow",
            Severity.UNSAFE: "red",
            Severity.CRITICAL: "bold red",
        }[finding.severity]

        table.add_row(
            f"[{style}]{finding.severity.value.upper()}[/{style}]",
            finding.message,
            finding.line_hint,
        )

    console.print(table)

    # Suggestions
    unsafe_suggestions = [
        f
        for f in all_findings
        if f.severity in (Severity.UNSAFE, Severity.CRITICAL) and f.suggestion
    ]
    if unsafe_suggestions:
        console.print("\n[bold]Suggested fixes:[/bold]\n")
        for finding in unsafe_suggestions:
            console.print(f"[cyan]{finding.suggestion}[/cyan]\n")

    # Exit code
    if strict:
        has_any = len(all_findings) > 0
    else:
        has_any = any(
            f.severity in (Severity.UNSAFE, Severity.CRITICAL) for f in all_findings
        )

    if has_any:
        console.print(
            f"\n[bold red]Found {len(all_findings)} issue(s) across {unsafe_files} "
            f"of {total_files} file(s).[/bold red]"
        )
        sys.exit(1)
    else:
        console.print(
            f"\n[bold yellow]Found {len(all_findings)} warning(s), "
            f"no UNSAFE/CRITICAL issues.[/bold yellow]"
        )
        sys.exit(0)


@main.command()
@click.argument("path", type=click.Path(exists=True))
def explain(path: str) -> None:
    """Explain findings in detail with safe migration patterns."""
    target = Path(path)

    if target.is_file():
        migrations = [parse_sql_file(target)]
    elif target.is_dir():
        migrations = parse_migration_directory(target)
    else:
        console.print(f"[red]Error:[/red] {path} is not a file or directory")
        sys.exit(1)

    if not migrations:
        console.print("[yellow]No .sql migration files found.[/yellow]")
        return

    console.print("[bold blue]━" * 60)
    console.print("[bold]MIGRATE-SAFE — Rolling Deployment Safety Report")
    console.print("[bold blue]━" * 60)

    for migration in migrations:
        findings = analyze_migration(migration)
        if not findings:
            console.print(f"\n[green]✓ {migration.path.name}[/green] — no issues")
            continue

        console.print(f"\n[yellow]⚠ {migration.path.name}[/yellow] — {len(findings)} finding(s):")
        for finding in findings:
            console.print(f"\n  [bold]{finding}[/bold]")


if __name__ == "__main__":
    main()
