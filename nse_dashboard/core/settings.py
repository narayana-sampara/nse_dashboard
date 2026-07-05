from __future__ import annotations

import os
from dataclasses import dataclass


def _as_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    app_name: str = "NSE Signal Desk"
    environment: str = "development"
    debug: bool = False
    log_level: str = "INFO"
    cache_seconds: int = 900
    default_period: str = "1y"
    cors_origins: tuple[str, ...] = ()
    redis_url: str | None = None
    database_url: str | None = None
    dependency_timeout_seconds: float = 2.0
    celery_broker_url: str | None = None
    celery_result_backend: str | None = None
    worker_schedule_seconds: int = 300
    worker_data_ttl_seconds: int = 3600
    idempotency_ttl_seconds: int = 86400
    idempotency_lock_ttl_seconds: int = 900
    websocket_tokens: tuple[str, ...] = ()
    websocket_heartbeat_seconds: int = 30
    stream_channel_prefix: str = "nse:stream"
    metrics_enabled: bool = True
    otel_service_name: str = "nse-dashboard-api"
    otel_exporter_otlp_endpoint: str | None = None
    otel_trace_sample_ratio: float = 0.1
    prediction_generate_timeout_seconds: float = 25.0
    jwt_secret: str = "dev-insecure-secret-change-me"
    jwt_expires_minutes: int = 480
    admin_bootstrap_username: str = "admin"
    admin_bootstrap_password: str | None = None
    five_percent_target_pct: float = 5.0
    five_percent_stop_loss_pct: float = 2.0
    five_percent_holding_days: int = 5
    five_percent_probability_threshold: float = 65.0
    five_percent_min_avg_volume: float = 0.0
    five_percent_min_avg_turnover: float = 10_000_000.0
    five_percent_max_candidates: int = 20

    @classmethod
    def from_environment(cls) -> "Settings":
        defaults = cls()
        origins = tuple(
            origin.strip()
            for origin in os.getenv("CORS_ORIGINS", "").split(",")
            if origin.strip()
        )
        settings = cls(
            app_name=os.getenv("APP_NAME", defaults.app_name),
            environment=os.getenv("APP_ENV", defaults.environment).lower(),
            debug=_as_bool(os.getenv("DEBUG", "0")),
            log_level=os.getenv("LOG_LEVEL", defaults.log_level).upper(),
            cache_seconds=int(os.getenv("CACHE_SECONDS", str(defaults.cache_seconds))),
            default_period=os.getenv("DEFAULT_PERIOD", defaults.default_period),
            cors_origins=origins,
            redis_url=os.getenv("REDIS_URL") or None,
            database_url=os.getenv("DATABASE_URL") or None,
            dependency_timeout_seconds=float(
                os.getenv("DEPENDENCY_TIMEOUT_SECONDS", str(defaults.dependency_timeout_seconds))
            ),
            celery_broker_url=os.getenv("CELERY_BROKER_URL") or None,
            celery_result_backend=os.getenv("CELERY_RESULT_BACKEND") or None,
            worker_schedule_seconds=int(
                os.getenv("WORKER_SCHEDULE_SECONDS", str(defaults.worker_schedule_seconds))
            ),
            worker_data_ttl_seconds=int(
                os.getenv("WORKER_DATA_TTL_SECONDS", str(defaults.worker_data_ttl_seconds))
            ),
            idempotency_ttl_seconds=int(
                os.getenv("IDEMPOTENCY_TTL_SECONDS", str(defaults.idempotency_ttl_seconds))
            ),
            idempotency_lock_ttl_seconds=int(
                os.getenv(
                    "IDEMPOTENCY_LOCK_TTL_SECONDS",
                    str(defaults.idempotency_lock_ttl_seconds),
                )
            ),
            websocket_tokens=tuple(
                token.strip()
                for token in os.getenv("WEBSOCKET_TOKENS", "").split(",")
                if token.strip()
            ),
            websocket_heartbeat_seconds=int(
                os.getenv(
                    "WEBSOCKET_HEARTBEAT_SECONDS",
                    str(defaults.websocket_heartbeat_seconds),
                )
            ),
            stream_channel_prefix=os.getenv(
                "STREAM_CHANNEL_PREFIX", defaults.stream_channel_prefix
            ).strip(),
            metrics_enabled=_as_bool(os.getenv("METRICS_ENABLED", "1")),
            otel_service_name=os.getenv(
                "OTEL_SERVICE_NAME", defaults.otel_service_name
            ).strip(),
            otel_exporter_otlp_endpoint=(
                os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") or None
            ),
            otel_trace_sample_ratio=float(
                os.getenv(
                    "OTEL_TRACE_SAMPLE_RATIO", str(defaults.otel_trace_sample_ratio)
                )
            ),
            prediction_generate_timeout_seconds=float(
                os.getenv(
                    "PREDICTION_GENERATE_TIMEOUT_SECONDS",
                    str(defaults.prediction_generate_timeout_seconds),
                )
            ),
            jwt_secret=os.getenv("JWT_SECRET", defaults.jwt_secret),
            jwt_expires_minutes=int(
                os.getenv("JWT_EXPIRES_MINUTES", str(defaults.jwt_expires_minutes))
            ),
            admin_bootstrap_username=os.getenv(
                "ADMIN_BOOTSTRAP_USERNAME", defaults.admin_bootstrap_username
            ),
            admin_bootstrap_password=os.getenv("ADMIN_BOOTSTRAP_PASSWORD") or None,
            five_percent_target_pct=float(
                os.getenv("FIVE_PERCENT_TARGET_PCT", str(defaults.five_percent_target_pct))
            ),
            five_percent_stop_loss_pct=float(
                os.getenv("FIVE_PERCENT_STOP_LOSS_PCT", str(defaults.five_percent_stop_loss_pct))
            ),
            five_percent_holding_days=int(
                os.getenv("FIVE_PERCENT_HOLDING_DAYS", str(defaults.five_percent_holding_days))
            ),
            five_percent_probability_threshold=float(
                os.getenv(
                    "FIVE_PERCENT_PROBABILITY_THRESHOLD",
                    str(defaults.five_percent_probability_threshold),
                )
            ),
            five_percent_min_avg_volume=float(
                os.getenv("FIVE_PERCENT_MIN_AVG_VOLUME", str(defaults.five_percent_min_avg_volume))
            ),
            five_percent_min_avg_turnover=float(
                os.getenv(
                    "FIVE_PERCENT_MIN_AVG_TURNOVER", str(defaults.five_percent_min_avg_turnover)
                )
            ),
            five_percent_max_candidates=int(
                os.getenv("FIVE_PERCENT_MAX_CANDIDATES", str(defaults.five_percent_max_candidates))
            ),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.environment not in {"development", "test", "staging", "production"}:
            raise ValueError(f"Unsupported APP_ENV: {self.environment}")
        if self.cache_seconds < 0:
            raise ValueError("CACHE_SECONDS must be zero or greater")
        if self.log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError(f"Unsupported LOG_LEVEL: {self.log_level}")
        if self.dependency_timeout_seconds <= 0:
            raise ValueError("DEPENDENCY_TIMEOUT_SECONDS must be greater than zero")
        if self.worker_schedule_seconds <= 0:
            raise ValueError("WORKER_SCHEDULE_SECONDS must be greater than zero")
        if self.worker_data_ttl_seconds <= 0:
            raise ValueError("WORKER_DATA_TTL_SECONDS must be greater than zero")
        if self.idempotency_ttl_seconds <= 0:
            raise ValueError("IDEMPOTENCY_TTL_SECONDS must be greater than zero")
        if self.idempotency_lock_ttl_seconds <= 0:
            raise ValueError("IDEMPOTENCY_LOCK_TTL_SECONDS must be greater than zero")
        if self.websocket_heartbeat_seconds <= 0:
            raise ValueError("WEBSOCKET_HEARTBEAT_SECONDS must be greater than zero")
        if not self.stream_channel_prefix:
            raise ValueError("STREAM_CHANNEL_PREFIX must not be empty")
        if not self.otel_service_name:
            raise ValueError("OTEL_SERVICE_NAME must not be empty")
        if not 0 <= self.otel_trace_sample_ratio <= 1:
            raise ValueError("OTEL_TRACE_SAMPLE_RATIO must be between zero and one")
        if self.prediction_generate_timeout_seconds <= 0:
            raise ValueError("PREDICTION_GENERATE_TIMEOUT_SECONDS must be greater than zero")
        if not self.jwt_secret:
            raise ValueError("JWT_SECRET must not be empty")
        if self.jwt_expires_minutes <= 0:
            raise ValueError("JWT_EXPIRES_MINUTES must be greater than zero")
        if not self.admin_bootstrap_username:
            raise ValueError("ADMIN_BOOTSTRAP_USERNAME must not be empty")
        if self.environment == "production" and self.jwt_secret == Settings.jwt_secret:
            raise ValueError("JWT_SECRET must be set explicitly in production")
        if self.five_percent_target_pct <= 0:
            raise ValueError("FIVE_PERCENT_TARGET_PCT must be greater than zero")
        if self.five_percent_stop_loss_pct <= 0:
            raise ValueError("FIVE_PERCENT_STOP_LOSS_PCT must be greater than zero")
        if self.five_percent_holding_days <= 0:
            raise ValueError("FIVE_PERCENT_HOLDING_DAYS must be greater than zero")
        if not 0 <= self.five_percent_probability_threshold <= 100:
            raise ValueError("FIVE_PERCENT_PROBABILITY_THRESHOLD must be between zero and 100")
        if self.five_percent_max_candidates <= 0:
            raise ValueError("FIVE_PERCENT_MAX_CANDIDATES must be greater than zero")
