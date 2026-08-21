"""create oauth tokens

Revision ID: c4d1f7a92b30
Revises: 82aa331b4c88
Create Date: 2026-08-21 18:40:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c4d1f7a92b30'
down_revision: str | Sequence[str] | None = '82aa331b4c88'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('oauth_tokens',
    sa.Column('provider', sa.String(length=32), nullable=False),
    sa.Column('access_token', sa.Text(), nullable=True),
    sa.Column('refresh_token', sa.Text(), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=True),
    sa.Column('refresh_expires_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.PrimaryKeyConstraint('provider')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('oauth_tokens')
