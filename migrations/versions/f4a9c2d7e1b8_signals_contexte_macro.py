"""signals : contexte macro (macro_level/macro_note, display-only)

Revision ID: f4a9c2d7e1b8
Revises: e3f8a1c6d904
Create Date: 2026-09-13 00:00:00.000000

signals.macro_level / signals.macro_note : contexte macro au moment du
signal (Macro Risk Engine display-only, MACRO.md §10). NULL = aucun
événement notable à proximité (tous les signaux antérieurs et la grande
majorité des signaux à venir : HIGH/EXTREME seulement sont persistés).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f4a9c2d7e1b8'
down_revision: Union[str, Sequence[str], None] = 'e3f8a1c6d904'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Ajoute les deux colonnes du contexte macro."""
    op.add_column("signals", sa.Column("macro_level", sa.String(8), nullable=True))
    op.add_column("signals", sa.Column("macro_note", sa.String(100), nullable=True))


def downgrade() -> None:
    """Retire les deux colonnes du contexte macro."""
    op.drop_column("signals", "macro_note")
    op.drop_column("signals", "macro_level")
