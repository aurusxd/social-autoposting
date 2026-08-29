"""add scheduled_at to posts

Revision ID: c4f1a7b9d2e5
Revises: 82aa331b4c88
Create Date: 2026-08-29 10:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c4f1a7b9d2e5'
down_revision: str | Sequence[str] | None = '82aa331b4c88'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('posts', sa.Column('scheduled_at', sa.DateTime(), nullable=True))
    op.create_index('idx_posts_scheduled_at', 'posts', ['scheduled_at'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('idx_posts_scheduled_at', table_name='posts')
    op.drop_column('posts', 'scheduled_at')
