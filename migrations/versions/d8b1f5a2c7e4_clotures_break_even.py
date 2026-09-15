"""paper_trades : raison de sortie BE (clôture break-even à 0R)

Revision ID: d8b1f5a2c7e4
Revises: f4a9c2d7e1b8
Create Date: 2026-09-15 00:00:00.000000

Nouvelle raison de sortie "BE" : position protégée au break-even (rappel
+1,5R émis, SL suggéré à l'entrée) puis prix revenu toucher l'entrée ->
clôture à l'entrée, résultat +0R. La contrainte CHECK de exit_reason est
élargie de ('TP', 'SL') à ('TP', 'SL', 'BE'). Colonne inchangée
(String(4), "BE" tient).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd8b1f5a2c7e4'
down_revision: Union[str, Sequence[str], None] = 'f4a9c2d7e1b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Élargit la contrainte CHECK exit_reason à ('TP', 'SL', 'BE')."""
    op.drop_constraint("ck_paper_trades_exit_reason", "paper_trades", type_="check")
    op.create_check_constraint(
        "ck_paper_trades_exit_reason",
        "paper_trades",
        "exit_reason IN ('TP', 'SL', 'BE')",
    )


def downgrade() -> None:
    """Retrouve la contrainte CHECK d'origine ('TP', 'SL')."""
    op.drop_constraint("ck_paper_trades_exit_reason", "paper_trades", type_="check")
    op.create_check_constraint(
        "ck_paper_trades_exit_reason",
        "paper_trades",
        "exit_reason IN ('TP', 'SL')",
    )
