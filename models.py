"""Database models belong here when persistence is introduced."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class SignalSnapshot:
    symbol: str
    strategy: str
    signal: str
    price: float
    created_at: datetime
