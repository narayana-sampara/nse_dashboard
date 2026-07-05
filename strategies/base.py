from abc import ABC, abstractmethod
from typing import Any

import pandas as pd


class Strategy(ABC):
    name: str

    @abstractmethod
    def evaluate(self, close: pd.Series) -> dict[str, Any]:
        """Return a signal and its latest calculated indicators."""
