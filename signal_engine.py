"""Backward-compatible facade for code importing the original SignalEngine."""

from nse_dashboard.infrastructure.cache import MemoryTtlCache
from nse_dashboard.infrastructure.yahoo import YahooFinanceAdapter
from nse_dashboard.services.signals import SignalService


class SignalEngine(SignalService):
    def __init__(self, cache_seconds: int = 900) -> None:
        super().__init__(
            adapter=YahooFinanceAdapter(),
            cache=MemoryTtlCache(),
            cache_seconds=cache_seconds,
        )

    @property
    def strategy_names(self) -> list[str]:
        return [self.strategy.name]
