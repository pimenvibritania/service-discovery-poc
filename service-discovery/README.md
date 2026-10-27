# Service Discovery — Kubernetes Dependency Mapper

Automatically scans your Kubernetes clusters and exports a comprehensive Excel workbook documenting every service's databases, inter-service dependencies, external APIs, and operational info.

---

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Dry-run with sample fixtures (no cluster needed)
python main.py --dry-run --env staging

# Scan live staging cluster
python main.py --context my-staging-context --env staging

# Scan production
python main.py --context my-prod-context --env production

# Compare staging vs production (diff mode)
python main.py --diff \
  --staging-context staging-ctx \
  --production-context production-ctx
```

---

## Output

The script produces `output/service_discovery_{env}_{date}.xlsx` with 6 sheets:

| Sheet | Contents |
|---|---|
| **Services Overview** | Master list — one row per service |
| **Database Connections** | Every DB connection (PG, Mongo, MySQL, ES) with host, port, instance ID, user |
| **Service Dependencies** | Inter-service calls via kube DNS, with validation |
| **External Dependencies** | Alibaba Cloud, Stripe, Twilio, SendGrid, Firebase, etc. |
| **Environment Diff** | Staging vs production drift — missing services, config drift, image differences |
| **Operations** | Manual fill — team owner, Slack channel, runbook, SLA, alerting |

---

## CLI Options

```
--context TEXT              Kubeconfig context to use
--env [staging|production]  Environment label (default: staging)
--staging-context TEXT      Context for staging (diff mode)
--production-context TEXT   Context for production (diff mode)
--namespaces TEXT           Comma-separated namespaces to scan (default: all)
--exclude TEXT              Comma-separated namespaces to exclude
--output-dir TEXT           Output directory (default: ./output)
--format [xlsx|json|both]   Output format (default: xlsx)
--diff                      Compare staging vs production
--dry-run                   Load from JSON fixtures (no live cluster)
--fixtures-dir TEXT         Fixtures directory (default: ./fixtures)
--verbose                   Debug logging
```

---

## Configuration

Edit `config.py` or set environment variables:

```bash
export STAGING_CONTEXT="my-staging-context"
export PRODUCTION_CONTEXT="my-prod-context"
export OUTPUT_DIR="./output"
```

To restrict to specific namespaces, edit `TARGET_NAMESPACES` in `config.py`.

---

## Running Tests

```bash
# All tests
PYTHONPATH=. pytest tests/ -v

# Unit tests only
PYTHONPATH=. pytest tests/ -v -k "not integration"

# Integration test only (generates a real .xlsx)
PYTHONPATH=. pytest tests/test_integration.py -v
```

**86 tests** covering: DB detection, service dependency detection, external dependency detection, environment diffing, all scanners, full pipeline, and Excel output validation.

---

## Security

- Secret values are **never read, stored, or logged** — only key names are used
- The script only needs `get`/`list` RBAC permissions on Deployments, StatefulSets, ConfigMaps, Secrets, Services, and Namespaces

```yaml
rules:
  - apiGroups: ["", "apps"]
    resources: ["deployments", "statefulsets", "configmaps", "secrets", "services", "namespaces"]
    verbs: ["get", "list"]
```

---

## Adding New Detection Patterns

All patterns live in `analyzers/db_detector.py` (`DB_PATTERNS`, `CACHE_PATTERNS`) and `analyzers/external_dep_detector.py` (`EXTERNAL_PATTERNS`). Add a new entry to the appropriate dict — no other code changes needed.
