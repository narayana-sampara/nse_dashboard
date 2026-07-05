# Service-level objectives

These objectives cover the public API and dashboard in production. They are
measured over rolling 30-day windows; planned maintenance is not excluded.

| Indicator | Objective | Measurement |
| --- | ---: | --- |
| API availability | 99.9% | Non-5xx responses to `/api/v1/*` divided by requests |
| API latency | 95% under 1 s | Prometheus request-duration histogram |
| Signal freshness | 99% within 10 min of schedule | Latest completed scan timestamp |
| Alert delivery | 99% within 60 s of computation | Alert creation minus scan completion |
| Backup success | 100% daily | Completed backup artifact and CronJob status |
| Restore readiness | RPO 24 h, RTO 2 h | Monthly restore drill |

The availability error budget is 43.2 minutes per 30 days. Page when the 5-minute
error ratio remains above 1% for 10 minutes or the API is absent for 2 minutes.
Create a ticket when p95 latency exceeds one second for 15 minutes. Freeze
non-remediation releases when more than 50% of the monthly error budget is spent,
and require a reliability review when it is exhausted.

Signal freshness and alert delivery require timestamp metrics from the scheduled
pipeline as follow-up instrumentation before those SLOs can page automatically;
until then, query the persisted scan and alert timestamps in the operations check.
