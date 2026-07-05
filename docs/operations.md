# Operations guide

## Local production stack

Run `docker compose up --build` to start TimescaleDB, Redis, the API and workers,
the Next.js dashboard on port 3000, Prometheus on port 9090, and Jaeger on port
16686. The API exports Prometheus data at `/metrics` and OTLP traces to Jaeger.
Set `OTEL_TRACE_SAMPLE_RATIO=1` only for short diagnostic sessions; the default is
10% to control storage and export overhead.

The legacy server-rendered page remains on port 8000 for compatibility. The
Next.js application is the phase 6 operations interface and includes current
signals, filters, persisted alerts, and an authenticated live alert stream.
`NEXT_PUBLIC_API_ORIGIN` must be the browser-visible API origin, not its internal
container address.

## Backups and restore

Run an on-demand local backup with:

```powershell
docker compose --profile operations run --rm backup
```

Backups use PostgreSQL custom format and are retained for seven days by default.
Set `BACKUP_RETENTION_DAYS` to change this. Production should place the backup PVC
on encrypted durable storage and copy completed dumps to an object store in a
separate failure domain. Test a restore at least monthly.

Restore into an empty database after stopping writers:

```powershell
pg_restore --clean --if-exists --no-owner --dbname $env:DATABASE_URL .\nse-dashboard-YYYYMMDDTHHMMSSZ.dump
py scripts/migrate.py
```

Verify `/health/ready`, a signal-history query, and the newest alert timestamp
before restoring traffic.

## Kubernetes deployment

1. Build and publish the API, UI, and `deploy/backup.Dockerfile` images.
2. Replace the example image names and host in `deploy/kubernetes/production.yaml`.
3. Create `nse-secrets` through the cluster secret manager using
   `secrets.example.yaml` only as a field reference. Never commit real values.
4. Run `kubectl apply -f deploy/kubernetes/secrets.yaml`, then
   `kubectl apply -k deploy/kubernetes`.
5. Confirm the migration Job completed before directing traffic to the API.

The manifest assumes managed PostgreSQL/TimescaleDB, managed Redis, an NGINX
Ingress controller, metrics-server for HPA, and an OTLP collector. Production
deployments should pin images by digest and apply network policies and pod
disruption budgets appropriate to the cluster.

## Incident triage

- API down: inspect `/health/live`, pod restarts, and `NseApiDown`.
- Dependency failure: inspect `/health/ready`; it identifies cache or database.
- Elevated errors: correlate the request ID in JSON logs with a Jaeger trace.
- Stale alerts: inspect Beat and each Celery queue, then the latest task key and
  `signal_alerts` timestamp. Do not rerun a stage by manually deleting task keys.
- Restore required: stop workers and API writes, restore, migrate, validate, then
  resume Beat followed by workers and API.
