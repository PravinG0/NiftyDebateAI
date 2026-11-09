"""
NiftyDebateAI agents package.

Exports:
    BullAgent   – generates bullish arguments from market data.
    BearAgent   – generates bearish arguments from market data.
    JudgeAgent  – evaluates both sides and decides on a trade.
"""

from agents.bull_agent import BullAgent
from agents.bear_agent import BearAgent
from agents.judge_agent import JudgeAgent

__all__ = ["BullAgent", "BearAgent", "JudgeAgent"]
