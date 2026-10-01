# migrate-safe

**Migration safety linter for Drizzle ORM** — detect unsafe SQL patterns before they break rolling deployments.

When you `DROP COLUMN` or `RENAME COLUMN` in a migration, old pods still reading that column crash while new pods have already removed it. `migrate-safe` catches these patterns in CI before they reach production.

## Install

```bash
pip install git+https://github.com/yunaremaia/migrate-safe.git
```

## Usage

```bash
# Check a single migration file
migrate-safe check 0001_create_users.sql

# Check a directory of migrations
migrate-safe check ./drizzle/migrations

# Explain findings in detail
migrate-safe explain ./drizzle/migrations

# Strict mode: fail on warnings too
migrate-safe check --strict ./drizzle/migrations
```

## What it detects

| Pattern | Severity | Why it breaks |
|---------|----------|---------------|
| `DROP COLUMN` | 🔴 UNSAFE | Old pods crash reading missing column |
| `RENAME COLUMN` | 🔴 CRITICAL | Old pods expect old name, new pods expect new |
| `ADD COLUMN NOT NULL` (no DEFAULT) | 🔴 UNSAFE | Fails on existing rows |
| `ALTER COLUMN TYPE` (risky cast) | 🟡 WARNING | May fail on incompatible data |
| `DROP TABLE` / `DROP TYPE` | 🟡 WARNING | May be referenced elsewhere |

## Safe migration patterns

### Instead of `DROP COLUMN`:
```sql
-- Step 1 (deploy): make nullable
ALTER TABLE users ALTER COLUMN email DROP NOT NULL;

-- Step 2 (next PR, after old pods gone):
ALTER TABLE users DROP COLUMN email;
```

### Instead of `RENAME COLUMN`:
```sql
-- Step 1: add new column
ALTER TABLE users ADD COLUMN full_name VARCHAR(255);

-- Step 2 (deploy): backfill
UPDATE users SET full_name = name;

-- Step 3: update app code to use full_name

-- Step 4 (next PR): drop old column
ALTER TABLE users DROP COLUMN name;
```

## GitHub Action

```yaml
- uses: yunaremaia/migrate-safe@main
  with:
    path: './drizzle/migrations'
```

## Exit codes

- `0` — no UNSAFE/CRITICAL findings (or no findings at all)
- `1` — UNSAFE or CRITICAL findings detected

## License

MIT
