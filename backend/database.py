"""
Database models and async engine/session setup for NiftyDebateAI.

Supports:
1. Production PostgreSQL (Render Postgres, Supabase, Neon) via asyncpg with connection pooling.
2. Automatic connection URL normalization (postgres:// or postgresql:// -> postgresql+asyncpg://, sslmode handling).
3. Local development and test fallback with SQLite + aiosqlite (WAL mode, busy timeout, NullPool).
4. Relational models:
   - UserModel (users table)
   - DebateSessionModel (debate_sessions table with user_id)
   - PaperTradeModel (paper_trades table with user_id)
   - UserVoteModel (user_debate_votes table)
   - UserWatchlistModel (user_watchlists table)
5. Idempotent dialect-aware schema migrations.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Optional

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
    event,
    func,
    inspect,
    text,
)
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, relationship
from sqlalchemy.pool import AsyncAdaptedQueuePool, NullPool

from config import settings
from time_utils import IST, ist_now, to_ist

logger = logging.getLogger("niftydebate.database")


class ISTDateTime(TypeDecorator):
    """SQLAlchemy TypeDecorator guaranteeing Asia/Kolkata timezone preservation
    across SQLite and PostgreSQL persistence and retrieval."""
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


# ── URL Normalization & Engine Factory ────────────────────────────────────────

def normalize_database_url(url: str) -> str:
    """Normalize raw connection strings for SQLAlchemy 2.x async engine compatibility.

    - Transforms 'postgres://' or 'postgresql://' into 'postgresql+asyncpg://'
    - Transforms 'sqlite://' into 'sqlite+aiosqlite://'
    - Normalizes 'sslmode=require' to 'ssl=require' for asyncpg
    """
    raw_url = str(url).strip()
    if raw_url.startswith("postgres://"):
        raw_url = raw_url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif raw_url.startswith("postgresql://") and not raw_url.startswith("postgresql+"):
        raw_url = raw_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    elif raw_url.startswith("sqlite://") and not raw_url.startswith("sqlite+"):
        raw_url = raw_url.replace("sqlite://", "sqlite+aiosqlite://", 1)

    # asyncpg query parameter adjustment
    if "postgresql+asyncpg://" in raw_url:
        if "sslmode=require" in raw_url:
            raw_url = raw_url.replace("sslmode=require", "ssl=require")

    return raw_url


def create_engine_for_url(url: str) -> AsyncEngine:
    """Create a configured AsyncEngine for either SQLite or PostgreSQL."""
    norm_url = normalize_database_url(url)
    if "sqlite" in norm_url:
        eng = create_async_engine(
            norm_url,
            echo=False,
            future=True,
            poolclass=NullPool,
            connect_args={"check_same_thread": False, "timeout": 30},
        )

        @event.listens_for(eng.sync_engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.close()

        return eng
    else:
        # Production PostgreSQL (Render Postgres, Supabase, Neon) with robust pooling
        return create_async_engine(
            norm_url,
            echo=False,
            future=True,
            poolclass=AsyncAdaptedQueuePool,
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,
            pool_recycle=300,
        )


DATABASE_URL_NORMALIZED = normalize_database_url(settings.DATABASE_URL)
IS_SQLITE = "sqlite" in DATABASE_URL_NORMALIZED
IS_POSTGRES = "postgresql" in DATABASE_URL_NORMALIZED

engine: AsyncEngine = create_engine_for_url(DATABASE_URL_NORMALIZED)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""
    pass


# ── Models ────────────────────────────────────────────────────────────────────

class UserModel(Base):
    """User account entity for authentication, preferences, and user-scoped data."""

    __tablename__ = "users"

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    email: str = Column(String(255), unique=True, index=True, nullable=False)
    username: str = Column(String(64), unique=True, index=True, nullable=False)
    hashed_password: str = Column(String(255), nullable=False)
    full_name: str | None = Column(String(128), nullable=True)
    is_active: bool = Column(Boolean, default=True, nullable=False)
    is_superuser: bool = Column(Boolean, default=False, nullable=False)
    created_at: datetime = Column(
        ISTDateTime,
        default=ist_now,
        server_default=func.now(),
        nullable=False,
    )
    updated_at: datetime = Column(
        ISTDateTime,
        default=ist_now,
        server_default=func.now(),
        onupdate=ist_now,
        nullable=False,
    )

    # Relationships
    debates = relationship("DebateSessionModel", back_populates="user")
    trades = relationship("PaperTradeModel", back_populates="user")
    votes = relationship("UserVoteModel", back_populates="user", cascade="all, delete-orphan")
    watchlists = relationship("UserWatchlistModel", back_populates="user", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<User id={self.id} username={self.username} email={self.email}>"


class DebateSessionModel(Base):
    """Stores each AI debate round including bull/bear reasoning and judge decision."""

    __tablename__ = "debate_sessions"

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    user_id: int | None = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    timestamp: datetime = Column(
        ISTDateTime,
        default=ist_now,
        server_default=func.now(),
        nullable=False,
    )
    spot_price: float = Column(Float, nullable=False)
    index_symbol: str = Column(String(16), nullable=False, default="NIFTY", server_default="NIFTY")
    market_data_json: str | None = Column(Text, nullable=True)

    # Bull agent
    bull_reasoning: str | None = Column(Text, nullable=True)
    bull_confidence: int | None = Column(Integer, nullable=True)
    bull_strike: str | None = Column(String(32), nullable=True)

    # Bear agent
    bear_reasoning: str | None = Column(Text, nullable=True)
    bear_confidence: int | None = Column(Integer, nullable=True)
    bear_strike: str | None = Column(String(32), nullable=True)

    # Judge
    judge_decision: str | None = Column(String(16), nullable=True)
    judge_reasoning: str | None = Column(Text, nullable=True)
    judge_full_output: str | None = Column(Text, nullable=True)

    # Relationships
    user = relationship("UserModel", back_populates="debates")
    trades = relationship(
        "PaperTradeModel",
        back_populates="debate_session",
        cascade="all, delete-orphan",
    )
    votes = relationship("UserVoteModel", back_populates="debate", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return (
            f"<DebateSession id={self.id} spot={self.spot_price} "
            f"decision={self.judge_decision}>"
        )


class PaperTradeModel(Base):
    """Paper (simulated) trade created from a debate decision or manual execution."""

    __tablename__ = "paper_trades"

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    user_id: int | None = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
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
    user = relationship("UserModel", back_populates="trades")
    debate_session = relationship("DebateSessionModel", back_populates="trades")

    def __repr__(self) -> str:
        return (
            f"<PaperTrade id={self.id} symbol={self.option_symbol} "
            f"status={self.status} pnl={self.pnl}>"
        )


class UserVoteModel(Base):
    """Community consensus vote on a debate session."""

    __tablename__ = "user_debate_votes"

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    user_id: int = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    debate_id: int = Column(Integer, ForeignKey("debate_sessions.id", ondelete="CASCADE"), nullable=False)
    vote: str = Column(String(16), nullable=False)  # "BULL", "BEAR", "AGREE_JUDGE", "DISAGREE_JUDGE"
    confidence: int | None = Column(Integer, nullable=True)  # 1-10
    comment: str | None = Column(Text, nullable=True)
    created_at: datetime = Column(
        ISTDateTime,
        default=ist_now,
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("user_id", "debate_id", name="uq_user_debate_vote"),
    )

    # Relationships
    user = relationship("UserModel", back_populates="votes")
    debate = relationship("DebateSessionModel", back_populates="votes")

    def __repr__(self) -> str:
        return f"<UserVote id={self.id} user_id={self.user_id} debate_id={self.debate_id} vote={self.vote}>"


class UserWatchlistModel(Base):
    """User-bookmarked strikes, contracts, and watchlists."""

    __tablename__ = "user_watchlists"

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    user_id: int = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    symbol: str = Column(String(16), nullable=False, default="NIFTY")
    strike: int = Column(Integer, nullable=False)
    option_type: str = Column(String(8), nullable=False)  # "CE" or "PE"
    notes: str | None = Column(Text, nullable=True)
    created_at: datetime = Column(
        ISTDateTime,
        default=ist_now,
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("user_id", "symbol", "strike", "option_type", name="uq_user_watchlist_item"),
    )

    # Relationships
    user = relationship("UserModel", back_populates="watchlists")

    def __repr__(self) -> str:
        return f"<UserWatchlist id={self.id} user_id={self.user_id} {self.symbol} {self.strike}{self.option_type}>"


# ── Helpers & Dialect-Aware Schema Migration ─────────────────────────────────

async def create_tables(target_engine: Optional[AsyncEngine] = None) -> None:
    """Create all database tables if they don't exist and ensure schema backward compatibility."""
    eng = target_engine or engine
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        # Synchronous dialect-aware column inspection and idempotent migration
        def _sync_migrate(sync_conn):
            insp = inspect(sync_conn)

            # Check debate_sessions columns
            try:
                ds_cols = {c["name"] for c in insp.get_columns("debate_sessions")}
                if "user_id" not in ds_cols:
                    sync_conn.execute(text("ALTER TABLE debate_sessions ADD COLUMN user_id INTEGER REFERENCES users(id)"))
                if "index_symbol" not in ds_cols:
                    sync_conn.execute(text("ALTER TABLE debate_sessions ADD COLUMN index_symbol VARCHAR(16) DEFAULT 'NIFTY'"))
            except Exception as e:
                logger.warning("Migration inspection notice for debate_sessions: %s", e)

            # Check paper_trades columns
            try:
                pt_cols = {c["name"] for c in insp.get_columns("paper_trades")}
                if "user_id" not in pt_cols:
                    sync_conn.execute(text("ALTER TABLE paper_trades ADD COLUMN user_id INTEGER REFERENCES users(id)"))
                if "trade_source" not in pt_cols:
                    sync_conn.execute(text("ALTER TABLE paper_trades ADD COLUMN trade_source VARCHAR(16) DEFAULT 'AI_DEBATE'"))
                if "index_symbol" not in pt_cols:
                    sync_conn.execute(text("ALTER TABLE paper_trades ADD COLUMN index_symbol VARCHAR(16) DEFAULT 'NIFTY'"))
                if "quantity" not in pt_cols:
                    sync_conn.execute(text("ALTER TABLE paper_trades ADD COLUMN quantity INTEGER DEFAULT 25"))
            except Exception as e:
                logger.warning("Migration inspection notice for paper_trades: %s", e)

        try:
            await conn.run_sync(_sync_migrate)
        except Exception as exc:
            logger.warning("Dialect column migration notice: %s", exc)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async session for dependency injection in FastAPI."""
    async with async_session_factory() as session:
        try:
            yield session
        finally:
            await session.close()
