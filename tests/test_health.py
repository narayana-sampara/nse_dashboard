from fastapi.testclient import TestClient

from nse_dashboard.api.app import create_app
from nse_dashboard.core.settings import Settings
from nse_dashboard.infrastructure.cache import MemoryTtlCache
from nse_dashboard.services.signals import SignalService
from tests.test_signal_service import FakeAdapter, FakeSnapshots


class UnavailableCache(MemoryTtlCache):
    def ping(self) -> bool:
        return False


def test_readiness_reports_dependency_failure() -> None:
    service = SignalService(FakeAdapter(), UnavailableCache(), snapshots=FakeSnapshots())
    with TestClient(create_app(Settings(environment="test"), service)) as client:
        response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "dependencies": {"cache": "unavailable", "database": "ok"},
    }


def test_readiness_reports_healthy_dependencies() -> None:
    service = SignalService(FakeAdapter(), MemoryTtlCache(), snapshots=FakeSnapshots())
    with TestClient(create_app(Settings(environment="test"), service)) as client:
        response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["dependencies"] == {"cache": "ok", "database": "ok"}


def test_metrics_count_requests_by_route() -> None:
    service = SignalService(FakeAdapter(), MemoryTtlCache(), snapshots=FakeSnapshots())
    with TestClient(create_app(Settings(environment="test"), service)) as client:
        assert client.get("/health/live").status_code == 200
        response = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert 'nse_http_requests_total{method="GET",route="/health/live",status="200"} 1' in response.text
    assert "nse_http_request_duration_seconds_bucket" in response.text


def test_metrics_can_be_disabled() -> None:
    service = SignalService(FakeAdapter(), MemoryTtlCache(), snapshots=FakeSnapshots())
    settings = Settings(environment="test", metrics_enabled=False)
    with TestClient(create_app(settings, service)) as client:
        response = client.get("/metrics")

    assert response.status_code == 404
