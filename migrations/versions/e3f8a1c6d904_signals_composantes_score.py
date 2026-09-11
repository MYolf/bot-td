"""signals : composantes du score (trend/momentum/macd/volume/structure/htf)

Revision ID: e3f8a1c6d904
Revises: c9d5b2f8e4a1
Create Date: 2026-09-11 00:00:00.000000

signals.score_* : composantes du score de qualité, persistées pour
l'affichage (champ « Setup » avec les indicateurs et leurs points, score
recalibré sur 100). NULL pour les signaux antérieurs (score brut en points)
et pour les composantes qu'une stratégie n'évalue pas.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e3f8a1c6d904'
down_revision: Union[str, Sequence[str], None] = 'c9d5b2f8e4a1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COMPOSANTES = (
    "score_trend",
    "score_momentum",
    "score_macd",
    "score_volume",
    "score_structure",
    "score_htf",
)


def upgrade() -> None:
    """Ajoute les six colonnes de composantes du score."""
    for colonne in _COMPOSANTES:
        op.add_column(
            "signals",
            sa.Column(colonne, sa.SmallInteger(), nullable=True),
        )


def downgrade() -> None:
    """Retire les six colonnes de composantes."""
    for colonne in reversed(_COMPOSANTES):
        op.drop_column("signals", colonne)
