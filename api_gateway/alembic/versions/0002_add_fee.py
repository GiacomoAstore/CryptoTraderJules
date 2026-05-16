"""add fee column

Revision ID: 0002_add_fee
Revises: 0001_initial
Create Date: 2026-05-10 18:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0002_add_fee'
down_revision: Union[str, None] = '0001_initial'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.add_column('trades', sa.Column('fee', sa.Float(), nullable=True, server_default='0.0'))

def downgrade() -> None:
    op.drop_column('trades', 'fee')
