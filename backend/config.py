"""
Configuration module for NiftyDebateAI.

Loads environment variables from the project-level .env file and exposes
a singleton ``settings`` instance used throughout the application.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from typing import Any
from pydantic import ConfigDict, Field
from pydantic_settings import BaseSettings

# Load .env from the project root (one level above backend/)
_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=_env_path)


class Settings(BaseSettings):
    """Application-wide settings sourced from environment variables."""

    model_config = ConfigDict(
        env_file=str(_env_path),
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # ── API Keys ──────────────────────────────────────────────────────────
    GEMINI_API_KEY: str = Field(default="your_gemini_api_key_here")
    GEMINI_MODEL: str = Field(default="gemini-3.5-flash")
    OPENALGO_API_KEY: str = Field(default="")
    OPENALGO_BASE_URL: str = Field(default="")
    DATABASE_URL: str = Field(default=f"sqlite+aiosqlite:///{_env_path.parent / 'niftydebate.db'}")

    # ── Safety / Sandbox Paper Trading ────────────────────────────────────
    FORCE_ANALYZER_MODE: bool = Field(default=True)
    LOT_SIZE: int = Field(default=25)

    # ── Market Hours (IST) ────────────────────────────────────────────────
    MARKET_OPEN: str = Field(default="09:20")
    MARKET_CLOSE: str = Field(default="15:15")
    TIMEZONE: str = Field(default="Asia/Kolkata")

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if "///./" in self.DATABASE_URL:
            rel = self.DATABASE_URL.split("///./", 1)[1]
            self.DATABASE_URL = f"sqlite+aiosqlite:///{(_env_path.parent / rel).resolve()}"
    DEBATE_INTERVAL_MINUTES: int = Field(default=5)
    MAX_POSITION_SIZE: int = Field(default=1)   # lots
    MAX_LOTS: int = Field(default=1)
    CAPITAL: float = Field(default=5000.0)      # virtual capital (INR) - Rs 5,000 budget
    BUDGET_LIMIT: float = Field(default=5000.0)
    DEFAULT_INDEX: str = Field(default="NIFTY")

    def model_post_init(self, __context: Any) -> None:
        super().model_post_init(__context)
        if self.DATABASE_URL.startswith("sqlite+aiosqlite:///./"):
            rel_name = self.DATABASE_URL.replace("sqlite+aiosqlite:///./", "")
            abs_path = (_env_path.parent / rel_name).resolve()
            object.__setattr__(self, "DATABASE_URL", f"sqlite+aiosqlite:///{abs_path}")


settings = Settings()


# ── Capital Investment Budget & Position Sizing Guardrails (R4) ───────────────
BUDGET_LIMIT: float = 5000.0
MAX_LOTS: int = 1


class BudgetExceededError(ValueError):
    """Raised when an order outlay exceeds the capital investment budget."""
    pass


# ── Multi-Index Configuration (R3) ────────────────────────────────────────────
INDEX_CONFIG: dict[str, dict[str, Any]] = {
    "NIFTY": {
        "symbol": "NIFTY",
        "scrip_id": 13,
        "lot_size": 25,
        "strike_step": 50,
        "exchange_segment": "IDX_I",
        "default_spot": 24986.40,
    },
    "BANKNIFTY": {
        "symbol": "BANKNIFTY",
        "scrip_id": 25,
        "lot_size": 15,
        "strike_step": 100,
        "exchange_segment": "IDX_I",
        "default_spot": 52140.80,
    },
}


def get_index_config(symbol: str = "NIFTY") -> dict[str, Any]:
    """Retrieve index parameters for symbol with fallback to NIFTY."""
    sym = (symbol or "NIFTY").upper()
    return INDEX_CONFIG.get(sym, INDEX_CONFIG["NIFTY"])


def validate_paper_trade_guardrails(
    symbol: str,
    entry_price: float,
    lots: int = 1,
    budget_limit: float = BUDGET_LIMIT,
) -> dict[str, Any]:
    """Validates 1-lot order enforcement and Rs 5,000 budget guardrail.

    Raises:
        ValueError / BudgetExceededError if guardrail conditions fail.
    """
    symbol_up = (symbol or "NIFTY").upper()
    if symbol_up not in INDEX_CONFIG:
        raise ValueError(f"Invalid index symbol: {symbol}")
    cfg = INDEX_CONFIG[symbol_up]
    lot_size = cfg["lot_size"]

    if lots != 1:
        raise ValueError(
            f"Strict 1-lot order enforcement violated: attempted {lots} lots. Only 1 lot allowed."
        )

    if entry_price <= 0:
        raise ValueError(
            f"Invalid option premium: Rs {entry_price:.2f}. Must be strictly positive."
        )

    quantity = lots * lot_size
    outlay = round(entry_price * quantity, 2)
    max_allowed_premium = round(budget_limit / lot_size, 2)

    if outlay > budget_limit:
        raise BudgetExceededError(
            f"BudgetExceededError: Outlay Rs {outlay:.2f} exceeds Rs {budget_limit:.2f} limit. "
            f"Max allowable premium for {symbol_up} is Rs {max_allowed_premium:.2f}."
        )

    return {
        "status": "APPROVED",
        "symbol": symbol_up,
        "lot_size": lot_size,
        "quantity": quantity,
        "entry_price": entry_price,
        "outlay": outlay,
        "max_allowed_premium": max_allowed_premium,
        "budget_limit": budget_limit,
    }

