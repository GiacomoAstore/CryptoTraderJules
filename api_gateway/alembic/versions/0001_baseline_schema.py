"""Baseline schema — single idempotent migration (source: db_schema.py)

Revision ID: 0001_baseline
Revises:
Create Date: 2026-05-17
"""
from typing import Sequence, Union

from alembic import op

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from db_schema import apply_baseline_sync, HEAD_REVISION  # noqa: E402

revision: str = HEAD_REVISION
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    apply_baseline_sync(conn)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ticks;")
    op.execute("DROP TABLE IF EXISTS positions;")
    op.execute("DROP TABLE IF EXISTS orders;")
    op.execute("DROP TABLE IF EXISTS daily_performance;")
    op.execute("DROP TABLE IF EXISTS ohlcv;")
    op.execute("DROP TABLE IF EXISTS order_commands;")
    op.execute("DROP TABLE IF EXISTS trades;")
