import pytest

from nse_dashboard.core.settings import Settings


def test_settings_parse_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("DEBUG", "true")
    monkeypatch.setenv("CACHE_SECONDS", "60")
    monkeypatch.setenv("CORS_ORIGINS", "https://one.example, https://two.example")
    monkeypatch.setenv("REDIS_URL", "redis://cache:6379/0")
    monkeypatch.setenv("DATABASE_URL", "postgresql://db/app")
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://broker:6379/1")
    monkeypatch.setenv("CELERY_RESULT_BACKEND", "redis://results:6379/2")
    monkeypatch.setenv("WEBSOCKET_TOKENS", "first, second")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4317")
    monkeypatch.setenv("OTEL_TRACE_SAMPLE_RATIO", "0.25")

    settings = Settings.from_environment()

    assert settings.environment == "staging"
    assert settings.debug is True
    assert settings.cache_seconds == 60
    assert settings.cors_origins == ("https://one.example", "https://two.example")
    assert settings.redis_url == "redis://cache:6379/0"
    assert settings.database_url == "postgresql://db/app"
    assert settings.celery_broker_url == "redis://broker:6379/1"
    assert settings.celery_result_backend == "redis://results:6379/2"
    assert settings.websocket_tokens == ("first", "second")
    assert settings.otel_exporter_otlp_endpoint == "http://collector:4317"
    assert settings.otel_trace_sample_ratio == 0.25


def test_settings_reject_negative_cache_ttl() -> None:
    with pytest.raises(ValueError, match="CACHE_SECONDS"):
        Settings(cache_seconds=-1).validate()


def test_settings_reject_non_positive_dependency_timeout() -> None:
    with pytest.raises(ValueError, match="DEPENDENCY_TIMEOUT_SECONDS"):
        Settings(dependency_timeout_seconds=0).validate()


def test_settings_reject_non_positive_worker_ttls() -> None:
    with pytest.raises(ValueError, match="WORKER_DATA_TTL_SECONDS"):
        Settings(worker_data_ttl_seconds=0).validate()
    with pytest.raises(ValueError, match="IDEMPOTENCY_TTL_SECONDS"):
        Settings(idempotency_ttl_seconds=0).validate()
    with pytest.raises(ValueError, match="IDEMPOTENCY_LOCK_TTL_SECONDS"):
        Settings(idempotency_lock_ttl_seconds=0).validate()


def test_settings_reject_invalid_trace_sample_ratio() -> None:
    with pytest.raises(ValueError, match="OTEL_TRACE_SAMPLE_RATIO"):
        Settings(otel_trace_sample_ratio=1.1).validate()
