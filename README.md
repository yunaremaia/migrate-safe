# migrate-safe — Migration Safety Linter for Drizzle ORM

Detects unsafe SQL migrations that break rolling deployments.

## Problem

Drizzle ORM (25k+ stars) generates SQL migrations that can break rolling deployments. When you `DROP COLUMN`, old pods crash. Django solved this with `django-migration-linter` — Drizzle has nothing equivalent.

## Install

```bash
pip install migrate-safe
```

## Usage

```bash
# Check a directory of migrations
migrate-safe check ./drizzle/migrations

# With config file
migrate-safe check . --config .migratesafe.toml

# Output as SARIF (for CI)
migrate-safe check . --format sarif --output results.sarif
```

## Detectors

| Code | Description | Severity |
|------|-------------|----------|
| `MS001` | `DROP COLUMN` without prior nullable step | error |
| `MS002` | `RENAME COLUMN` (breaks old pods immediately) | error |
| `MS003` | `ADD COLUMN NOT NULL` without default | error |
| `MS004` | `ALTER COLUMN TYPE` with incompatible cast | warning |
| `MS005` | `DROP TABLE` that may still be referenced | warning |

## Config (`.migratesafe.toml`)

```toml
[migrate-safe]
# Allow known-safe patterns
allow = ["MS005"]

# Ignore specific migrations (already deployed)
ignore-files = ["0001_initial.sql"]

# Require two-step migration for DROP COLUMN
require-two-step = true
```

## Exit Codes

- `0` — all clear
- `1` — unsafe migrations found

## License

MIT
