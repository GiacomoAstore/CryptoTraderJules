#!/usr/bin/env python3
"""One-shot repair for DBs stamped on old migration chain (0001_initial … 0004_execution_tables)."""
from __future__ import annotations

import asyncio
import logging
import os
import sys

import asyncpg

from db_schema import HEAD_REVISION, apply_baseline_async

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("AlembicRepair")

LEGACY_REVISIONS = frozenset({
    "0001_initial",
    "0002_add_fee",
    "0003",
    "0004_execution_tables",
})


def _dsn() -> str:
    user = os.getenv("DB_USER", "crypto_user")
    password = os.getenv("DB_PASSWORD", "crypto_pass")
    host = os.getenv("DB_HOST", "timescaledb")
    port = os.getenv("DB_PORT", "5432")
    db = os.getenv("DB_NAME", "cryptoscalper_db")
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"


async def _ensure_alembic_table(conn) -> None:
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS alembic_version (
            version_num VARCHAR(32) NOT NULL,
            CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
        )
        """
    )


async def repair() -> bool:
    conn = await asyncpg.connect(_dsn())
    try:
        has_table = await conn.fetchval("SELECT to_regclass('public.alembic_version')")
        current = None
        if has_table:
            row = await conn.fetchrow("SELECT version_num FROM alembic_version LIMIT 1")
            current = row["version_num"] if row else None

        if current == HEAD_REVISION:
            logger.info("Alembic already at baseline %s", HEAD_REVISION)
            return True

        if has_table is None:
            logger.info("No alembic_version table — bootstrapping schema + revision")
        elif current in LEGACY_REVISIONS:
            logger.info("Repairing legacy revision %r → %s", current, HEAD_REVISION)
        elif current is not None:
            logger.warning("Unknown revision %r — re-stamping to baseline", current)

        await apply_baseline_async(conn)
        await _ensure_alembic_table(conn)
        await conn.execute("DELETE FROM alembic_version")
        await conn.execute(
            "INSERT INTO alembic_version (version_num) VALUES ($1)",
            HEAD_REVISION,
        )
        logger.info("Schema repair / stamp complete at %s", HEAD_REVISION)
        return True
    finally:
        await conn.close()


def main() -> int:
    try:
        asyncio.run(repair())
        return 0
    except Exception as exc:
        logger.error("Legacy repair failed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
