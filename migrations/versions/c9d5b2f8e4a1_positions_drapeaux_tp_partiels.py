"""positions : drapeaux TP1/TP2 notifiés (sorties partielles)

Revision ID: c9d5b2f8e4a1
Revises: b7e2d4c1a9f3
Create Date: 2026-09-10 00:00:00.000000

paper_positions.tp1_notified / tp2_notified : anti-spam des rappels de
sorties partielles (+1R / +2R) — un embed par niveau et par position,
tant qu'elle est ouverte.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c9d5b2f8e4a1'
down_revision: Union[str, Sequence[str], None] = 'b7e2d4c1a9f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Ajoute les drapeaux de notification TP1/TP2."""
    op.add_column('paper_positions',
        sa.Column('tp1_notified', sa.Boolean(), nullable=False,
                  server_default=sa.false()))
    op.add_column('paper_positions',
        sa.Column('tp2_notified', sa.Boolean(), nullable=False,
                  server_default=sa.false()))


def downgrade() -> None:
    """Retire les deux drapeaux."""
    op.drop_column('paper_positions', 'tp2_notified')
    op.drop_column('paper_positions', 'tp1_notified')
