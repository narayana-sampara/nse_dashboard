from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from nse_dashboard.domain.market_data import BrokerInstrument, MarketDataAdapter
from nse_dashboard.infrastructure.brokers.angel_one import AngelOneAdapter
from nse_dashboard.infrastructure.brokers.base import ReconnectPolicy
from nse_dashboard.infrastructure.brokers.shoonya import ShoonyaAdapter
from nse_dashboard.infrastructure.brokers.upstox import UpstoxAdapter

_ADAPTERS = {
    "angel_one": AngelOneAdapter,
    "shoonya": ShoonyaAdapter,
    "upstox": UpstoxAdapter,
}


def create_broker_adapter(
    provider: str,
    client: Any,
    instruments: Mapping[str, BrokerInstrument],
    *,
    rate_limit_per_second: float | None = None,
    rate_limit_burst: int = 1,
    reconnect_policy: ReconnectPolicy | None = None,
) -> MarketDataAdapter:
    """Create an adapter around an already authenticated provider SDK client."""

    key = provider.strip().lower().replace("-", "_").replace(" ", "_")
    adapter_type = _ADAPTERS.get(key)
    if adapter_type is None:
        supported = ", ".join(sorted(_ADAPTERS))
        raise ValueError(f"Unsupported broker provider {provider!r}; use one of: {supported}")
    kwargs: dict[str, Any] = {
        "rate_limit_burst": rate_limit_burst,
        "reconnect_policy": reconnect_policy,
    }
    if rate_limit_per_second is not None:
        kwargs["rate_limit_per_second"] = rate_limit_per_second
    return adapter_type(client, dict(instruments), **kwargs)
