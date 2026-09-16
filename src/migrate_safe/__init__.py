"""migrate-safe: Migration safety linter for Drizzle ORM.

Detects unsafe SQL migration patterns that can break rolling deployments
when old pods are still running while new migrations are applied.
"""
__version__ = "0.1.0"
