# Production architecture migration

The target is the six-layer architecture in the supplied design. The migration is incremental so every phase leaves a runnable system.

## Current state: phase 6 foundation complete

- FastAPI transport with versioned and backward-compatible routes
- Provider-neutral `MarketDataAdapter` contract
- Yahoo Finance isolated as the first adapter
- Computation service separated from ingestion
- Thread-safe TTL cache behind a dedicated implementation
- Liveness, readiness, request IDs, and structured JSON logs
- Provider-neutral Angel One, Shoonya, and Upstox broker adapters
- Shared token-bucket rate limiting and exponential reconnect backoff
- Strict OHLCV validation, chronological ordering, and timestamp deduplication
- Provider-neutral option tick model and chain analytics for Greeks, OI/PCR,
  max pain, GEX, VWAP, and unusual activity
- Redis pub/sub event delivery for signals, alerts, and options analytics
- Bearer-token-authenticated WebSocket channels with heartbeat messages
- Separately deployable Next.js signal dashboard and live alert center
- Stock-price dashboard quotes routed through FastAPI, sourced from Yahoo
  Finance, cached briefly, and persisted as backend quote snapshots
- Prometheus HTTP/WebSocket metrics and optional OTLP distributed tracing
- Automated PostgreSQL backups, Kubernetes manifests, operational runbook, and SLOs

Phase 1 now adds a shared Redis cache, PostgreSQL/TimescaleDB signal and market-scan
snapshots, ordered SQL migrations, local Docker Compose services, and readiness checks
for configured infrastructure. If `REDIS_URL` or `DATABASE_URL` is absent, the app
retains its lightweight local behavior (memory cache or disabled persistence).
Transient market-data failure is reported as HTTP 502.

Phase 2 adds a Celery pipeline with dedicated `ingestion`, `computation`,
`snapshots`, and `alerts` queues. Celery Beat schedules the pipeline; stages pass
Redis keys rather than large market-data payloads. Redis execution locks/results
and transactional PostgreSQL task keys make retries idempotent. Signal alerts are
stored in `signal_alerts` and exposed through `GET /api/v1/alerts`.

## Planned phases

1. **Redis and PostgreSQL/TimescaleDB** (foundation complete): cache interface, migrations, historical snapshots, Docker Compose, and dependency-aware readiness. Follow-up production hardening includes connection pooling, retention policies, and backup automation.
2. **Workers** (foundation complete): Celery queues for scheduled ingestion, computation, snapshots, and alerts with idempotent task keys. Follow-up hardening includes dead-letter handling, queue-depth metrics, and market-calendar-aware scheduling.
3. **Broker adapters** (foundation complete): Angel One, Shoonya, and Upstox historical-data adapters with shared rate limiting, reconnect backoff, validation, and deduplication. Authenticated SDK clients and symbol-to-instrument mappings are injected at startup so secrets remain outside the domain and service layers. Follow-up streaming integration is part of phase 5.
4. **Options analytics** (foundation complete): unified option tick model,
   Black-Scholes Greeks, OI/PCR and change metrics, max pain, dealer GEX, VWAP,
   threshold-based unusual-activity modules, and a 20-day normalized Smart Money
   Score. Normalized chains can be analyzed through
   `POST /api/v1/options/analytics`; daily contract histories are ranked through
   `POST /api/v1/options/smart-money`.
5. **Streaming API** (foundation complete): Redis pub/sub consumers and
   authenticated WebSocket channels for signals, alerts, and options analytics.
   Follow-up hardening includes token rotation through an identity provider,
   per-channel authorization, connection metrics, and replayable Redis Streams.
6. **Dashboard expansion and operations** (foundation complete): separately
   deployable Next.js UI and alert center, Prometheus metrics and alerts, OTLP
   tracing, PostgreSQL backup/restore automation, Docker Compose and Kubernetes
   deployment assets, operations runbook, and production SLOs. Follow-up hardening
   includes identity-provider token delivery, off-site backup replication, queue
   metrics, network policies, and automated freshness/delivery SLO indicators.

## Run locally

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app:app --host 127.0.0.1 --port 8000 --reload
```

Operational endpoints are `/health/live` and `/health/ready`. OpenAPI is available at `/docs` outside production.
Prometheus metrics are exposed at `/metrics`. The Next.js operations dashboard is
available at `http://localhost:3000` in Docker Compose; Prometheus and Jaeger are at
ports 9090 and 16686. See `docs/operations.md` and `docs/slos.md`.

Real-time clients connect to `/api/v1/stream/{channel}`, where `channel` is
`signals`, `alerts`, or `options`. Supply a configured token either as an
`Authorization: Bearer <token>` header or, for browser clients that cannot set
WebSocket headers, as the `?token=<token>` query parameter. Messages use a common
envelope with `channel`, `type`, `timestamp`, and `data` fields. Configure accepted
tokens with the comma-separated `WEBSOCKET_TOKENS` setting.

Smart Money ranking requires at least 20 daily observations per option contract,
including implied volatility, bid, and ask. Each latest factor is min-max
normalized to its contract's trailing 20-day range (a flat range is neutral at
50), then combined as volume ratio 30%, OI change 25%, IV momentum 20%, absolute
GEX contribution 15%, and bid-ask tightness 10%. Results are ordered by
`smart_money_score` descending and published as `options.smart_money` events.

Weekly predictions are generated at 16:00 Asia/Kolkata on weekdays and are also
available on demand through `POST /api/v1/weekly-predictions/generate`. The
versioned explainable baseline ranks liquid stocks across all price ranges using five- and
20-day momentum, EMA trend, RSI, volume and volatility. Prediction runs and
individual sector picks are persisted by migration `003_weekly_predictions.sql`;
the latest picks and symbol history are exposed through
`GET /api/v1/weekly-predictions` and
`GET /api/v1/weekly-predictions/{symbol}/history`. These are research estimates,
not guaranteed returns. A trained, walk-forward-validated model can replace the
baseline behind the same service contract and model-version fields.

Monthly predictions are stored independently for each selected 1-12 month
horizon. Generate or retrieve them through `POST` or `GET`
`/api/v1/monthly-predictions?horizon_months=N`; symbol history is available at
`/api/v1/monthly-predictions/{symbol}/history`. The explainable score totals 100
points after conversion to month-end bars: 3/6/12-month EMA trend 30,
horizon-adjusted monthly momentum 30, volume 10, monthly RSI quality 10, and
monthly volatility/12-month drawdown risk control 20. The dashboard exposes the interval selector,
total score, component bars, expected return, risk, and ranking reasons. Migration
`004_monthly_predictions.sql` persists run metadata and sector picks.

To run the Phase 1 stack, copy `.env.example` to `.env` for any local overrides,
then use `docker compose up --build`. The one-shot `migrate`
service applies migrations before the API starts. Signal evaluations are retained
and available at `GET /api/v1/signals/{symbol}/history?limit=100`; completed market
scans are retained in `market_scan_snapshots`.

The Compose stack starts one worker for each queue and a separate Beat scheduler.
The default pipeline interval is five minutes and can be changed with
`WORKER_SCHEDULE_SECONDS`. Run a pipeline immediately with:

```powershell
docker compose exec worker-ingestion celery -A nse_dashboard.workers.celery_app:app call workers.run_market_pipeline
```
