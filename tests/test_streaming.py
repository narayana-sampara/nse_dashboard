import asyncio

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from nse_dashboard.api.app import create_app
from nse_dashboard.core.settings import Settings
from nse_dashboard.infrastructure.cache import MemoryTtlCache
from nse_dashboard.services.signals import SignalService
from nse_dashboard.streaming.broker import MemoryEventBroker, event_message
from tests.test_signal_service import FakeAdapter, FakeSnapshots


def _service() -> SignalService:
    return SignalService(FakeAdapter(), MemoryTtlCache(), snapshots=FakeSnapshots())


def test_event_message_has_common_envelope() -> None:
    message = event_message("signals", "signals.updated", {"count": 2})

    assert message["channel"] == "signals"
    assert message["type"] == "signals.updated"
    assert message["data"] == {"count": 2}
    assert message["timestamp"].endswith("+00:00")


def test_memory_broker_fans_out_to_subscribers() -> None:
    async def scenario() -> None:
        broker = MemoryEventBroker()
        async with broker.subscribe("alerts") as messages:
            delivered = await broker.publish("alerts", "alerts.updated", {"count": 1})
            message = await anext(messages)
        assert delivered == 1
        assert message["data"] == {"count": 1}

    asyncio.run(scenario())


def test_websocket_rejects_missing_token() -> None:
    app = create_app(
        Settings(environment="test", websocket_tokens=("secret",)),
        _service(),
        MemoryEventBroker(),
    )
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/api/v1/stream/signals"):
                pass

    assert exc_info.value.code == 4401


def test_websocket_accepts_token_and_sends_heartbeat() -> None:
    app = create_app(
        Settings(
            environment="test",
            websocket_tokens=("secret",),
            websocket_heartbeat_seconds=1,
        ),
        _service(),
        MemoryEventBroker(),
    )
    with TestClient(app) as client:
        with client.websocket_connect("/api/v1/stream/signals?token=secret") as websocket:
            assert websocket.receive_json() == {"type": "heartbeat", "channel": "signals"}
