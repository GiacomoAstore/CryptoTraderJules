"""Initial tables

Revision ID: 0001_initial
Revises: 
Create Date: 2026-05-10 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0001_initial'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    # Trades Hypertable
    op.create_table(
        'trades',
        sa.Column('time', sa.DateTime(timezone=True), nullable=False),
        sa.Column('id', sa.String(50), nullable=False),
        sa.Column('symbol', sa.String(20), nullable=False),
        sa.Column('side', sa.String(10), nullable=False),
        sa.Column('entry_price', sa.Float(), nullable=False),
        sa.Column('exit_price', sa.Float(), nullable=False),
        sa.Column('quantity', sa.Float(), nullable=False),
        sa.Column('pnl_usdt', sa.Float(), nullable=False),
        sa.Column('pnl_pct', sa.Float(), nullable=False),
        sa.Column('open_time', sa.DateTime(timezone=True), nullable=False),
        sa.Column('close_time', sa.DateTime(timezone=True), nullable=False),
        sa.Column('strategy_name', sa.String(50), nullable=False),
        sa.Column('stop_loss_price', sa.Float(), nullable=True),
        sa.Column('take_profit_price', sa.Float(), nullable=True),
        sa.Column('close_reason', sa.String(50), nullable=False),
        sa.PrimaryKeyConstraint('time', 'id')
    )
    op.execute("SELECT create_hypertable('trades', 'time', if_not_exists => TRUE);")
    op.create_index('ix_trades_symbol', 'trades', ['symbol'])
    op.create_index('ix_trades_strategy', 'trades', ['strategy_name'])

    # Order Commands Audit Log
    op.create_table(
        'order_commands',
        sa.Column('id', sa.String(50), primary_key=True),
        sa.Column('timestamp', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('symbol', sa.String(20), nullable=False),
        sa.Column('type', sa.String(10), nullable=False),
        sa.Column('price', sa.Float(), nullable=False),
        sa.Column('quantity', sa.Float(), nullable=False),
        sa.Column('strategy', sa.String(50), nullable=False),
        sa.Column('status', sa.String(20), nullable=False)
    )

    # OHLCV Minute Aggregations Hypertable
    op.create_table(
        'ohlcv',
        sa.Column('time', sa.DateTime(timezone=True), nullable=False),
        sa.Column('symbol', sa.String(20), nullable=False),
        sa.Column('open', sa.Float(), nullable=False),
        sa.Column('high', sa.Float(), nullable=False),
        sa.Column('low', sa.Float(), nullable=False),
        sa.Column('close', sa.Float(), nullable=False),
        sa.Column('volume', sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint('time', 'symbol')
    )
    op.execute("SELECT create_hypertable('ohlcv', 'time', if_not_exists => TRUE);")

    # Daily Performance Aggregation
    op.create_table(
        'daily_performance',
        sa.Column('date', sa.Date(), primary_key=True),
        sa.Column('total_pnl', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('win_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('loss_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('win_rate', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('max_drawdown', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('sharpe_ratio', sa.Float(), nullable=False, server_default='0.0')
    )

def downgrade() -> None:
    op.drop_table('daily_performance')
    op.drop_table('ohlcv')
    op.drop_table('order_commands')
    op.drop_table('trades')
