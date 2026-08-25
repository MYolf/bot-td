"""Modèles SQLAlchemy des tables du projet (Phase 11).

Aucune logique métier ici : uniquement la description du schéma.
Conventions : prix en Numeric (jamais Float), timestamps UTC avec timezone,
historique immuable (on désactive une stratégie via `enabled`, jamais de
suppression en cascade).
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    SmallInteger,
    String,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base déclarative commune."""


class Strategy(Base):
    __tablename__ = "strategies"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    version: Mapped[str] = mapped_column(String(50), default="1")
    description: Mapped[str] = mapped_column(String(500), default="")
    enabled: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Signal(Base):
    __tablename__ = "signals"
    __table_args__ = (
        CheckConstraint("action IN ('BUY', 'SELL')", name="ck_signals_action"),
        CheckConstraint(
            "status IN ('RECEIVED', 'VALIDATED', 'SENT', 'REJECTED', 'DUPLICATE', 'ERROR')",
            name="ck_signals_status",
        ),
        Index("ix_signals_symbol_timestamp", "symbol", "signal_timestamp"),
        Index("ix_signals_strategy_timestamp", "strategy_id", "signal_timestamp"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # Cœur de la déduplication (Phase 10) : contrainte UNIQUE en base.
    signal_uid: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    strategy_id: Mapped[int] = mapped_column(
        ForeignKey("strategies.id"), index=True
    )
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    exchange: Mapped[str] = mapped_column(String(32))
    timeframe: Mapped[str] = mapped_column(String(10), index=True)
    action: Mapped[str] = mapped_column(String(4))
    entry_price: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    stop_loss: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    take_profit: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    risk_reward: Mapped[Decimal] = mapped_column(Numeric(10, 4))
    # Score de qualité (Phase 26) : NULL si la stratégie n'envoie aucune
    # composante. Indicateur interne, jamais une probabilité de gain.
    score: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    signal_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    status: Mapped[str] = mapped_column(String(12))
    discord_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class PaperPosition(Base):
    """Position simulée (Phase 21) : jamais d'ordre réel."""

    __tablename__ = "paper_positions"
    __table_args__ = (
        CheckConstraint("status IN ('OPEN', 'CLOSED')", name="ck_paper_positions_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id"), index=True)
    status: Mapped[str] = mapped_column(String(8), default="OPEN")
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    result_r: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)


class PaperTrade(Base):
    """Clôture simulée d'une position (Phase 21)."""

    __tablename__ = "paper_trades"
    __table_args__ = (
        CheckConstraint("exit_reason IN ('TP', 'SL')", name="ck_paper_trades_exit_reason"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    paper_position_id: Mapped[int] = mapped_column(
        ForeignKey("paper_positions.id"), index=True
    )
    exit_reason: Mapped[str] = mapped_column(String(4))
    exit_price: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    closed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
