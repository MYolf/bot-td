"""signals : colonne score (Phase 26 — score de qualité optionnel)

Revision ID: a1f4c8e27b91
Revises: 5d0c3c19d403
Create Date: 2026-08-25 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1f4c8e27b91'
down_revision: Union[str, Sequence[str], None] = '5d0c3c19d403'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Ajoute signals.score (SmallInteger, NULL = aucun score envoyé)."""
    op.add_column('signals',
        sa.Column('score', sa.SmallInteger(), nullable=True))


def downgrade() -> None:
    """Supprime signals.score."""
    op.drop_column('signals', 'score')
