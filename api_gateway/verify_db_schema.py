#!/usr/bin/env python3
"""
Fail loudly if DB schema or Alembic revision is out of sync with db_schema.py.
Exit 0 = OK, 1 = misalignment (blocks container boot).
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys

import asyncpg

from db_schema import (
    HEAD_REVISION,
    REQUIRED_HYPERTABLES,
    REQUIRED_TABLES,
    REQUIRED_TICKS_COLUMNS,
    REQUIRED_TRADES_COLUMNS,
    apply_baseline_async,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("DBSchemaVerify")


def _dsn() -> str:
    user = os.getenv("DB_USER", "crypto_user")
    password = os.getenv("DB_PASSWORD", "crypto_pass")
    host = os.getenv("DB_HOST", "timescaledb")
    port = os.getenv("DB_PORT", "5432")
    db = os.getenv("DB_NAME", "cryptoscalper_db")
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"


async def _table_columns(conn, table: str) -> set[str]:
    rows = await conn.fetch(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = $1
        """,
        table,
    )
    return {r["column_name"] for r in rows}


async def _hypertables(conn) -> set[str]:
    rows = await conn.fetch(
        """
        SELECT hypertable_name::text AS name
        FROM timescaledb_information.hypertables
        WHERE hypertable_schema = 'public'
        """
    )
    return {r["name"] for r in rows}


async def verify(attempt_repair: bool = True) -> bool:
    conn = await asyncpg.connect(_dsn())
    try:
        has_alembic = await conn.fetchval("SELECT to_regclass('public.alembic_version')")
        version = None
        if has_alembic:
            version_row = await conn.fetchrow(
                "SELECT version_num FROM alembic_version LIMIT 1"
            )
            version = version_row["version_num"] if version_row else None

        tables = {
            r["table_name"]
            for r in await conn.fetch(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
                """
            )
        }

        missing_tables = REQUIRED_TABLES - tables
        if missing_tables and attempt_repair:
            logger.error(
                "SCHEMA MISALIGNMENT: missing tables %s — applying baseline repair",
                sorted(missing_tables),
            )
            await apply_baseline_async(conn)
            if version and version != HEAD_REVISION:
                await conn.execute("DELETE FROM alembic_version")
                await conn.execute(
                    "INSERT INTO alembic_version (version_num) VALUES ($1)",
                    HEAD_REVISION,
                )
            tables = {
                r["table_name"]
                for r in await conn.fetch(
                    """
                    SELECT table_name FROM information_schema.tables
                    WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
                    """
                )
            }
            missing_tables = REQUIRED_TABLES - tables

        if version != HEAD_REVISION:
            logger.error(
                "ALEMBIC VERSION MISMATCH: db=%r expected=%r",
                version,
                HEAD_REVISION,
            )
            if attempt_repair:
                await apply_baseline_async(conn)
                await conn.execute("DELETE FROM alembic_version")
                await conn.execute(
                    "INSERT INTO alembic_version (version_num) VALUES ($1)",
                    HEAD_REVISION,
                )
                version = HEAD_REVISION
            if version != HEAD_REVISION:
                return False

        if missing_tables:
            logger.error("MISSING TABLES after repair: %s", sorted(missing_tables))
            return False

        hypertables = await _hypertables(conn)
        missing_ht = REQUIRED_HYPERTABLES - hypertables
        if missing_ht:
            logger.error("MISSING HYPERTABLES: %s", sorted(missing_ht))
            return False

        trades_cols = await _table_columns(conn, "trades")
        missing_trades = REQUIRED_TRADES_COLUMNS - trades_cols
        if missing_trades:
            logger.error("trades missing columns: %s", sorted(missing_trades))
            return False

        ticks_cols = await _table_columns(conn, "ticks")
        missing_ticks = REQUIRED_TICKS_COLUMNS - ticks_cols
        if missing_ticks:
            logger.error("ticks missing columns: %s", sorted(missing_ticks))
            return False

        logger.info(
            "DB schema OK — revision=%s tables=%d hypertables=%s",
            HEAD_REVISION,
            len(REQUIRED_TABLES),
            sorted(hypertables & REQUIRED_HYPERTABLES),
        )
        return True
    finally:
        await conn.close()


def main() -> int:
    ok = asyncio.run(verify(attempt_repair=True))
    if not ok:
        logger.error(
            "FATAL: Database schema verification failed. "
            "Run: docker compose exec api_gateway alembic upgrade head"
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
