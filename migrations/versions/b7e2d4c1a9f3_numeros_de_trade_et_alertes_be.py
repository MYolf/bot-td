"""signaux : numéro de trade séquentiel ; positions : drapeau BE notifié

Revision ID: b7e2d4c1a9f3
Revises: a1f4c8e27b91
Create Date: 2026-09-10 00:00:00.000000

- signals.sequence_number : numéro unique attribué à l'insertion (max + 1).
  Les lignes existantes sont backfillées 1..N par ordre d'arrivée (id
  croissant) : le prochain signal porte donc N+1.
- paper_positions.be_notified : anti-spam des rappels break-even (+1,5R) —
  une seule alerte par position.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7e2d4c1a9f3'
down_revision: Union[str, Sequence[str], None] = 'a1f4c8e27b91'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Numéro de trade + drapeau BE notifié."""
    op.add_column('signals',
        sa.Column('sequence_number', sa.Integer(), nullable=True))
    # Backfill : numéro = rang d'arrivée (id croissant).
    op.execute(
        "UPDATE signals SET sequence_number = "
        "(SELECT COUNT(*) FROM signals s2 WHERE s2.id <= signals.id)"
    )
    op.alter_column('signals', 'sequence_number', nullable=False)
    op.create_unique_constraint(
        'uq_signals_sequence_number', 'signals', ['sequence_number']
    )
    op.add_column('paper_positions',
        sa.Column('be_notified', sa.Boolean(), nullable=False,
                  server_default=sa.false()))


def downgrade() -> None:
    """Retire les deux colonnes."""
    op.drop_column('paper_positions', 'be_notified')
    op.drop_constraint('uq_signals_sequence_number', 'signals', type_='unique')
    op.drop_column('signals', 'sequence_number')
