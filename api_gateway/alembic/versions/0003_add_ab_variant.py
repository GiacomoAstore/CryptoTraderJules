"""add ab_variant to trades

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-10 22:33:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column('trades', sa.Column('ab_variant', sa.String(), server_default='A', nullable=False))

def downgrade() -> None:
    op.drop_column('trades', 'ab_variant')
