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
    ANGEL_API_KEY: str = Field(default="")
    ANGEL_CLIENT_CODE: str = Field(default="")
    ANGEL_PIN: str = Field(default="")
    ANGEL_TOTP_SECRET: str = Field(default="")

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
MAX_STOP_LOSS_INR: float = 700.0   # Maximum allowable stop-loss loss in Rupees (INR)
MAX_TARGET_INR: float = 1000.0     # Maximum allowable target gain in Rupees (INR)


class BudgetExceededError(ValueError):
    """Raised when an order outlay exceeds the capital investment budget."""
    pass


class RiskLimitExceededError(ValueError):
    """Raised when stop-loss or target exceeds configured INR limits."""
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


def compute_clamped_sl_target(
    symbol: str,
    entry_price: float,
    stop_loss: Optional[float] = None,
    target: Optional[float] = None,
    max_sl_inr: float = MAX_STOP_LOSS_INR,
    max_target_inr: float = MAX_TARGET_INR,
) -> tuple[float, float]:
    """Computes and clamps stop-loss and target according to max INR risk and reward guardrails.

    - Stop-Loss: Maximum loss is capped at Rs 700.
      For Nifty (25 qty): max SL distance is 28.00 pts (Entry - 28.00).
      For Bank Nifty (15 qty): max SL distance is 46.67 pts (Entry - 46.67).
    - Target: Maximum profit is capped at Rs 1,000.
      For Nifty (25 qty): max Target distance is 40.00 pts (Entry + 40.00).
      For Bank Nifty (15 qty): max Target distance is 66.67 pts (Entry + 66.67).
    """
    cfg = get_index_config(symbol)
    lot_size = cfg["lot_size"]
    import math
    max_sl_pts = math.floor((max_sl_inr / lot_size) * 100) / 100
    max_tgt_pts = math.floor((max_target_inr / lot_size) * 100) / 100

    min_allowed_sl = max(1.0, round(entry_price - max_sl_pts, 2))
    if stop_loss is None or stop_loss <= 0 or stop_loss < min_allowed_sl or stop_loss >= entry_price:
        final_sl = min_allowed_sl
    else:
        final_sl = round(stop_loss, 2)

    max_allowed_tgt = round(entry_price + max_tgt_pts, 2)
    if target is None or target <= entry_price or target > max_allowed_tgt:
        final_tgt = max_allowed_tgt
    else:
        final_tgt = round(target, 2)

    return final_sl, final_tgt


def validate_paper_trade_guardrails(
    symbol: str,
    entry_price: float,
    lots: int = 1,
    budget_limit: float = BUDGET_LIMIT,
    stop_loss: Optional[float] = None,
    target: Optional[float] = None,
    max_sl_inr: float = MAX_STOP_LOSS_INR,
    max_target_inr: float = MAX_TARGET_INR,
) -> dict[str, Any]:
    """Validates 1-lot order enforcement, Rs 5,000 budget, max Rs 700 SL, and max Rs 1,000 Target guardrail.

    Raises:
        ValueError / BudgetExceededError / RiskLimitExceededError if guardrail conditions fail.
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

    # Validate Stop Loss limit (Max Rs 700 loss)
    actual_sl = stop_loss
    if stop_loss is not None and stop_loss > 0:
        if stop_loss >= entry_price:
            raise ValueError(f"Stop-loss price (Rs {stop_loss:.2f}) must be strictly below entry price (Rs {entry_price:.2f}).")
        loss_inr = round((entry_price - stop_loss) * quantity, 2)
        if loss_inr > (max_sl_inr + 0.10):
            raise RiskLimitExceededError(
                f"Stop loss risk Rs {loss_inr:.2f} exceeds maximum allowed limit of Rs {max_sl_inr:.2f}."
            )

    # Validate Target limit (Max Rs 1,000 profit)
    actual_tgt = target
    if target is not None and target > 0:
        if target <= entry_price:
            raise ValueError(f"Target price (Rs {target:.2f}) must be strictly above entry price (Rs {entry_price:.2f}).")
        profit_inr = round((target - entry_price) * quantity, 2)
        if profit_inr > (max_target_inr + 0.10):
            raise RiskLimitExceededError(
                f"Target profit Rs {profit_inr:.2f} exceeds maximum allowed limit of Rs {max_target_inr:.2f}."
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
        "stop_loss": actual_sl,
        "target": actual_tgt,
        "max_sl_inr": max_sl_inr,
        "max_target_inr": max_target_inr,
    }

