from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal


FactorCoverage = Literal["FULL", "PARTIAL", "STALE", "NOT_APPLICABLE", "MISSING"]


@dataclass(frozen=True, slots=True)
class FactorInput:
    score: float | None
    coverage: FactorCoverage = "MISSING"
    features: dict[str, Any] = field(default_factory=dict)
    contributions: dict[str, float] = field(default_factory=dict)
    known_at: datetime | None = None

    @property
    def usable(self) -> bool:
        return self.score is not None and self.coverage in {"FULL", "PARTIAL"}


@dataclass(frozen=True, slots=True)
class AlphaFeatureSet:
    fundamental: FactorInput = field(default_factory=lambda: FactorInput(None))
    sentiment: FactorInput = field(default_factory=lambda: FactorInput(None))
    legal: FactorInput = field(default_factory=lambda: FactorInput(None))
    options: FactorInput = field(default_factory=lambda: FactorInput(None))


def normalize_symbol(symbol: str) -> str:
    value = symbol.strip().upper()
    if not value:
        raise ValueError("symbol is required")
    if "." not in value:
        value = f"{value}.NS"
    if not value.endswith((".NS", ".BO")):
        raise ValueError("Use an NSE .NS or BSE .BO ticker")
    stem = value.rsplit(".", 1)[0]
    if not stem or not all(character.isalnum() or character in {"-", "&"} for character in stem):
        raise ValueError("Invalid NSE/BSE ticker")
    return value


def exchange_for_symbol(symbol: str) -> str:
    return "BSE" if symbol.upper().endswith(".BO") else "NSE"
