from __future__ import annotations

import logging
from collections import defaultdict
from threading import Lock
from typing import TYPE_CHECKING

from fastapi import FastAPI

if TYPE_CHECKING:
    from nse_dashboard.core.settings import Settings

logger = logging.getLogger("nse_dashboard.observability")


class ApiMetrics:
    """Small, dependency-free Prometheus collector scoped to one app instance."""

    _buckets = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)

    def __init__(self) -> None:
        self._lock = Lock()
        self._requests: dict[tuple[str, str, int], int] = defaultdict(int)
        self._durations: dict[tuple[str, str], list[float]] = defaultdict(list)
        self._websockets: dict[str, int] = defaultdict(int)
        self._counters: dict[str, int] = defaultdict(int)
        self._gauges: dict[str, float] = defaultdict(float)
        self._custom_durations: dict[str, list[float]] = defaultdict(list)

    def observe_request(self, method: str, route: str, status: int, seconds: float) -> None:
        with self._lock:
            self._requests[(method, route, status)] += 1
            self._durations[(method, route)].append(seconds)

    def websocket_opened(self, channel: str) -> None:
        with self._lock:
            self._websockets[channel] += 1

    def websocket_closed(self, channel: str) -> None:
        with self._lock:
            self._websockets[channel] = max(0, self._websockets[channel] - 1)

    def increment(self, name: str, amount: int = 1) -> None:
        with self._lock:
            self._counters[name] += amount

    def set_gauge(self, name: str, value: float) -> None:
        with self._lock:
            self._gauges[name] = value

    def observe_duration(self, name: str, seconds: float) -> None:
        with self._lock:
            self._custom_durations[name].append(seconds)

    def render(self) -> str:
        with self._lock:
            requests = dict(self._requests)
            durations = {key: tuple(values) for key, values in self._durations.items()}
            websockets = dict(self._websockets)
            counters = dict(self._counters)
            gauges = dict(self._gauges)
            custom_durations = {key: tuple(values) for key, values in self._custom_durations.items()}
        lines = [
            "# HELP nse_http_requests_total Total HTTP requests.",
            "# TYPE nse_http_requests_total counter",
        ]
        for (method, route, status), count in sorted(requests.items()):
            labels = f'method="{method}",route="{_escape(route)}",status="{status}"'
            lines.append(f"nse_http_requests_total{{{labels}}} {count}")
        lines.extend(
            [
                "# HELP nse_http_request_duration_seconds HTTP request latency.",
                "# TYPE nse_http_request_duration_seconds histogram",
            ]
        )
        for (method, route), values in sorted(durations.items()):
            labels = f'method="{method}",route="{_escape(route)}"'
            for bucket in self._buckets:
                count = sum(value <= bucket for value in values)
                lines.append(
                    f'nse_http_request_duration_seconds_bucket{{{labels},le="{bucket}"}} {count}'
                )
            lines.append(
                f'nse_http_request_duration_seconds_bucket{{{labels},le="+Inf"}} {len(values)}'
            )
            lines.append(f"nse_http_request_duration_seconds_sum{{{labels}}} {sum(values):.9f}")
            lines.append(f"nse_http_request_duration_seconds_count{{{labels}}} {len(values)}")
        lines.extend(
            [
                "# HELP nse_websocket_connections Active WebSocket connections.",
                "# TYPE nse_websocket_connections gauge",
            ]
        )
        for channel, count in sorted(websockets.items()):
            lines.append(f'nse_websocket_connections{{channel="{_escape(channel)}"}} {count}')
        for name, count in sorted(counters.items()):
            lines.append(f"# TYPE {name} counter")
            lines.append(f"{name} {count}")
        for name, value in sorted(gauges.items()):
            lines.append(f"# TYPE {name} gauge")
            lines.append(f"{name} {value}")
        for name, values in sorted(custom_durations.items()):
            lines.append(f"# TYPE {name} histogram")
            for bucket in self._buckets:
                count = sum(value <= bucket for value in values)
                lines.append(f'{name}_bucket{{le="{bucket}"}} {count}')
            lines.append(f'{name}_bucket{{le="+Inf"}} {len(values)}')
            lines.append(f"{name}_sum {sum(values):.9f}")
            lines.append(f"{name}_count {len(values)}")
        return "\n".join(lines) + "\n"


def configure_tracing(app: FastAPI, settings: Settings) -> None:
    """Export FastAPI spans over OTLP when an endpoint is configured."""
    if not settings.otel_exporter_otlp_endpoint:
        return
    try:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
    except ImportError:  # pragma: no cover - catches an invalid production image
        logger.exception("OpenTelemetry endpoint configured but dependencies are unavailable")
        return

    provider = TracerProvider(
        resource=Resource.create({"service.name": settings.otel_service_name}),
        sampler=ParentBased(TraceIdRatioBased(settings.otel_trace_sample_ratio)),
    )
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint))
    )
    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')
