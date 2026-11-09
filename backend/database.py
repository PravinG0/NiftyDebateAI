"""
Database models and async engine/session setup for NiftyDebateAI.

Uses SQLAlchemy 2.x async API with aiosqlite as the backend.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, AsyncGenerator

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    TypeDecorator,
    event,
    func,
    text,
)
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, relationship

from sqlalchemy.pool import NullPool

from config import settings
from time_utils import IST, ist_now, to_ist


class ISTDateTime(TypeDecorator):
    """SQLAlchemy TypeDecorator guaranteeing Asia/Kolkata timezone preservation
    across SQLite persistence and retrieval."""
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        if value is not None and isinstance(value, (datetime, str)):
            return to_ist(value)
        return value

    def process_result_value(self, value: Any, dialect: Any) -> Any:
        if value is not None and isinstance(value, (datetime, str)):
            return to_ist(value)
        return value




# ── Engine & Session Factory ──────────────────────────────────────────────────

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    future=True,
    poolclass=NullPool,
    connect_args={"check_same_thread": False, "timeout": 30} if "sqlite" in settings.DATABASE_URL else {},
)

if "sqlite" in settings.DATABASE_URL:
    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""
    pass


# ── Models ────────────────────────────────────────────────────────────────────

class DebateSessionModel(Base):
    """Stores each AI debate round including bull/bear reasoning and judge decision."""

    __tablename__ = "debate_sessions"

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    timestamp: datetime = Column(
        ISTDateTime,
        default=ist_now,
        server_default=func.now(),
        nullable=False,
    )
    spot_price: float = Column(Float, nullable=False)
    index_symbol: str = Column(String(16), nullable=False, default="NIFTY", server_default="NIFTY")
    market_data_json: str = Column(Text, nullable=True)

    # Bull agent
    bull_reasoning: str = Column(Text, nullable=True)
    bull_confidence: int = Column(Integer, nullable=True)
    bull_strike: str = Column(String(32), nullable=True)

    # Bear agent
    bear_reasoning: str = Column(Text, nullable=True)
    bear_confidence: int = Column(Integer, nullable=True)
    bear_strike: str = Column(String(32), nullable=True)

    # Judge
    judge_decision: str = Column(String(16), nullable=True)
    judge_reasoning: str = Column(Text, nullable=True)
    judge_full_output: str = Column(Text, nullable=True)

    # Relationships
    trades = relationship(
        "PaperTradeModel",
        back_populates="debate_session",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return (
            f"<DebateSession id={self.id} spot={self.spot_price} "
            f"decision={self.judge_decision}>"
        )


class PaperTradeModel(Base):
    """Paper (simulated) trade created from a debate decision."""

    __tablename__ = "paper_trades"

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    debate_id: int = Column(Integer, ForeignKey("debate_sessions.id"), nullable=False)

    option_symbol: str = Column(String(64), nullable=False)
    index_symbol: str = Column(String(16), nullable=False, default="NIFTY", server_default="NIFTY")
    trade_type: str = Column(String(16), nullable=False)  # BUY_CE / BUY_PE
    entry_price: float = Column(Float, nullable=False)
    exit_price: float | None = Column(Float, nullable=True)
    stop_loss: float = Column(Float, nullable=False)
    target: float = Column(Float, nullable=False)
    pnl: float | None = Column(Float, nullable=True)
    status: str = Column(String(16), nullable=False, default="OPEN")  # OPEN / CLOSED / STOPPED_OUT / TARGET_HIT
    opened_at: datetime = Column(
        ISTDateTime,
        default=ist_now,
        server_default=func.now(),
        nullable=False,
    )
    closed_at: datetime | None = Column(ISTDateTime, nullable=True)
    quantity: int = Column(Integer, nullable=False, default=25, server_default="25")
    trade_source: str = Column(String(16), nullable=False, default="AI_DEBATE")  # AI_DEBATE or MANUAL

    # Relationships
    debate_session = relationship("DebateSessionModel", back_populates="trades")

    def __repr__(self) -> str:
        return (
            f"<PaperTrade id={self.id} symbol={self.option_symbol} "
            f"status={self.status} pnl={self.pnl}>"
        )


# ── Helpers ───────────────────────────────────────────────────────────────────

async def create_tables() -> None:
    """Create all database tables if they don't exist and ensure columns."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        migrations = [
            "ALTER TABLE paper_trades ADD COLUMN trade_source VARCHAR(16) DEFAULT 'AI_DEBATE'",
            "ALTER TABLE debate_sessions ADD COLUMN index_symbol VARCHAR(16) DEFAULT 'NIFTY'",
            "ALTER TABLE paper_trades ADD COLUMN index_symbol VARCHAR(16) DEFAULT 'NIFTY'",
            "ALTER TABLE paper_trades ADD COLUMN quantity INTEGER DEFAULT 25",
        ]
        for sql in migrations:
            try:
                await conn.execute(text(sql))
            except Exception:
                pass


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async session for dependency injection in FastAPI."""
    async with async_session_factory() as session:
        try:
            yield session
        finally:
            await session.close()
