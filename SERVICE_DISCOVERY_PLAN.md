# Service Discovery Knowledge Base — Execution Plan

## Project Context

**Problem:** Mekari runs 80+ microservices on Kubernetes (Alibaba Cloud ACK) across staging and production environments. There is no centralized knowledge about service dependencies, database connections, or inter-service communication. When debugging issues, engineers waste time tracing dependency chains manually.

**Goal:** Build an automated discovery script that extracts service metadata from Kubernetes and outputs a comprehensive Excel workbook — the single source of truth for all service dependencies and operational information.

**Example:** `nocode-service` connects to 2 databases (PostgreSQL instance X with user Y, and MongoDB), plus calls `auth-service` and `billing-service` via kube DNS. Today, you'd have to SSH into the pod and read env vars to figure this out. After this project, it's one spreadsheet lookup.

---

## Infrastructure Summary

| Attribute | Value |
|---|---|
| Cloud provider | Alibaba Cloud ACK |
| Cluster access | kubeconfig + context switching, behind VPN + bastion |
| Environments | staging, production (separate contexts) |
| Namespace strategy | Per team/domain (e.g., `billing`, `auth`, `nocode`) |
| Config storage | Mix of ConfigMaps + Secrets (originally from HashiCorp Vault, materialized as K8s Secrets) |
| Inter-service comms | HTTP/gRPC via kube DNS (no service mesh, no message queues) |
| Operational metadata | Does not exist yet — create empty columns for manual enrichment |

---

## Phase 1: Discovery Script

### 1.1 Project Structure

```
service-discovery/
├── README.md                    # Usage instructions
├── requirements.txt             # Python dependencies
├── config.py                    # Configuration & constants
├── main.py                      # Entry point / CLI
├── discovery/
│   ├── __init__.py
│   ├── k8s_client.py            # Kubernetes API client wrapper
│   ├── deployment_scanner.py    # Scan deployments & pods
│   ├── configmap_scanner.py     # Scan ConfigMaps
│   ├── secret_scanner.py        # Scan Secrets (metadata only, NOT values)
│   ├── env_parser.py            # Parse env vars into structured data
│   └── service_scanner.py       # Scan K8s Service objects
├── models/
│   ├── __init__.py
│   └── service_profile.py       # Data classes for service profiles
├── analyzers/
│   ├── __init__.py
│   ├── db_detector.py           # Detect database connections from env vars
│   ├── service_dep_detector.py  # Detect inter-service dependencies
│   ├── external_dep_detector.py # Detect external API/service dependencies
│   └── env_differ.py            # Compare staging vs production
└── exporters/
    ├── __init__.py
    └── excel_exporter.py        # Generate the .xlsx workbook
```

### 1.2 Dependencies (`requirements.txt`)

```
kubernetes>=28.0.0          # Official K8s Python client
openpyxl>=3.1.0             # Excel file generation
click>=8.0.0                # CLI framework
rich>=13.0.0                # Pretty console output during scan
pydantic>=2.0.0             # Data validation for service profiles
```

### 1.3 Configuration (`config.py`)

Define the following constants and make them overridable via CLI flags or environment variables:

```python
# Kubernetes contexts (from kubeconfig)
STAGING_CONTEXT = "staging-context-name"      # User must configure
PRODUCTION_CONTEXT = "production-context-name" # User must configure

# Namespaces to scan (None = scan all namespaces)
# If set, only these namespaces are scanned
TARGET_NAMESPACES = None  # e.g., ["billing", "auth", "nocode", "platform"]

# Namespaces to always skip
EXCLUDE_NAMESPACES = [
    "kube-system",
    "kube-public", 
    "kube-node-lease",
    "istio-system",
    "monitoring",
    "logging",
    "cert-manager",
    "ingress-nginx",
]

# Resource types to scan
SCAN_DEPLOYMENTS = True
SCAN_STATEFULSETS = True
SCAN_DAEMONSETS = False     # Usually infra, not application services

# Output
OUTPUT_DIR = "./output"
OUTPUT_FILENAME = "service_discovery_{env}_{date}.xlsx"
```

### 1.4 Kubernetes Client Wrapper (`k8s_client.py`)

**Purpose:** Abstract away context switching and API calls.

**Key behaviors:**
- Accept a `context_name` parameter to switch between staging/production
- Use `kubernetes.config.load_kube_config(context=context_name)`
- Provide methods: `list_deployments(namespace)`, `list_configmaps(namespace)`, `list_secrets(namespace)`, `list_services(namespace)`, `list_namespaces()`
- Handle pagination for large clusters (80+ services means potentially hundreds of configmaps)
- Implement retry logic with exponential backoff for API timeouts
- Log progress using `rich` console

### 1.5 Deployment Scanner (`deployment_scanner.py`)

**Purpose:** Extract basic service metadata from Deployments (and optionally StatefulSets).

**For each Deployment, extract:**
- `name` — deployment name
- `namespace` — which namespace it lives in
- `replicas` — desired replica count
- `image` — container image(s) and tag(s)
- `labels` — all labels (useful for team ownership later)
- `annotations` — all annotations
- `resource_requests` — CPU/memory requests
- `resource_limits` — CPU/memory limits
- `ports` — container ports exposed
- `env_vars` — ALL environment variables (from direct values, ConfigMapKeyRef, SecretKeyRef)
- `env_from` — ConfigMaps/Secrets mounted via `envFrom`
- `volume_mounts` — any mounted volumes (may contain config files)
- `health_checks` — liveness/readiness probe endpoints

**Important:** For env vars that reference ConfigMaps or Secrets (via `valueFrom.configMapKeyRef` or `valueFrom.secretKeyRef`), resolve the actual value by looking up the referenced ConfigMap/Secret. This is critical — most DB hostnames and service URLs will be stored this way.

### 1.6 ConfigMap Scanner (`configmap_scanner.py`)

**Purpose:** Scan ConfigMaps to find env vars that weren't directly embedded in deployments.

**Key behaviors:**
- For each namespace, list all ConfigMaps
- Parse the `data` field — each key-value pair is potentially an env var
- Cross-reference with deployment `envFrom` references to associate ConfigMaps with services
- Store results for the env parser to analyze

### 1.7 Secret Scanner (`secret_scanner.py`)

**Purpose:** Scan Secrets for metadata only.

**CRITICAL SECURITY RULE:** Never log, store, or export actual secret values (passwords, tokens, connection strings with credentials). Only extract:
- Secret name
- Secret keys (not values) — e.g., knowing a secret has a key `DATABASE_URL` tells us there's a DB connection without exposing the password
- Which deployments reference this secret
- Secret type (Opaque, kubernetes.io/tls, etc.)

**For dependency detection:** When a secret key suggests a DB connection (e.g., `POSTGRES_PASSWORD`, `MONGO_URI`), flag it as "has database dependency" without reading the actual value. The DB hostname should come from the ConfigMap or direct env var, not the secret.

### 1.8 Environment Variable Parser (`env_parser.py`)

**Purpose:** The core intelligence — parse raw env vars into structured dependency information.

**This is the most critical file.** It takes the raw env vars collected from deployments + resolved ConfigMap/Secret references and categorizes them.

**Detection rules (implement as a pattern registry so new patterns can be added easily):**

#### Database Detection Patterns

```python
DB_PATTERNS = {
    "postgresql": {
        "env_keys": [
            r"(?i)(postgres|pgsql|pg)_(host|hostname|server|url|uri|addr)",
            r"(?i)database_(host|url)",
            r"(?i)db_(host|url|server)",
            r"(?i)rds_(host|endpoint)",
        ],
        "url_schemes": ["postgres://", "postgresql://"],
        "port_default": 5432,
        "user_keys": [r"(?i)(postgres|pgsql|pg|db|database)_(user|username)"],
        "database_keys": [r"(?i)(postgres|pgsql|pg|db)_(database|dbname|name|db)"],
        "port_keys": [r"(?i)(postgres|pgsql|pg|db|database)_(port)"],
    },
    "mongodb": {
        "env_keys": [
            r"(?i)mongo(db)?_(host|hostname|server|url|uri|addr)",
            r"(?i)mongodb_uri",
        ],
        "url_schemes": ["mongodb://", "mongodb+srv://"],
        "port_default": 27017,
        "user_keys": [r"(?i)mongo(db)?_(user|username)"],
        "database_keys": [r"(?i)mongo(db)?_(database|db|dbname)"],
    },
    "redis": {
        "env_keys": [
            r"(?i)redis_(host|hostname|server|url|uri|addr)",
            r"(?i)redis_url",
            r"(?i)cache_(host|url)",
        ],
        "url_schemes": ["redis://", "rediss://"],
        "port_default": 6379,
    },
    "elasticsearch": {
        "env_keys": [
            r"(?i)(elasticsearch|elastic|es)_(host|hostname|url|uri|addr)",
        ],
        "url_schemes": ["http://", "https://"],  # matched with host containing "elastic"
        "port_default": 9200,
    },
    "mysql": {
        "env_keys": [
            r"(?i)mysql_(host|hostname|server|url|uri|addr)",
        ],
        "url_schemes": ["mysql://", "mysql+pymysql://"],
        "port_default": 3306,
        "user_keys": [r"(?i)mysql_(user|username)"],
        "database_keys": [r"(?i)mysql_(database|db|dbname)"],
    },
}
```

#### Service Dependency Detection Patterns

```python
# Kube DNS patterns — this is how services find each other
KUBE_DNS_PATTERNS = [
    # Full form: service-name.namespace.svc.cluster.local
    r"(?P<service>[a-z0-9-]+)\.(?P<namespace>[a-z0-9-]+)\.svc\.cluster\.local",
    # Short form: service-name.namespace
    r"(?P<service>[a-z0-9-]+)\.(?P<namespace>[a-z0-9-]+)\.svc",
    # Values that look like internal hostnames (not IPs, not external domains)
    # Heuristic: no dots OR single segment that matches a known service name
]

# Env key patterns that suggest a service dependency
SERVICE_ENV_KEYS = [
    r"(?i)([A-Z_]+)_(HOST|HOSTNAME|URL|URI|ENDPOINT|BASE_URL|API_URL|SERVICE_URL|ADDR)",
    r"(?i)([A-Z_]+)_(GRPC_HOST|GRPC_ADDR|GRPC_ENDPOINT)",
]
```

#### External Dependency Detection

```python
# If a URL/hostname doesn't match kube DNS and isn't a known DB, it's external
# Classify by domain patterns:
EXTERNAL_PATTERNS = {
    "alibaba_cloud": [r"\.aliyuncs\.com", r"\.alibaba\.com"],
    "aws": [r"\.amazonaws\.com", r"\.aws\.amazon\.com"],
    "third_party_api": [r"api\.", r"\.io/", r"\.com/api"],
    "cdn_storage": [r"\.oss-", r"\.cdn\."],  # Alibaba OSS
}
```

**Output of env_parser:** For each service, produce a structured dict:

```python
{
    "service_name": "nocode-service",
    "namespace": "nocode",
    "databases": [
        {
            "type": "postgresql",
            "host": "rm-xxx.pg.rds.aliyuncs.com",
            "port": 5432,
            "user": "nocode_user",
            "database": "nocode_db",
            "source_env_key": "PG_HOST",
            "source_type": "configmap",  # or "secret", "direct"
            "source_name": "nocode-config",
        },
        {
            "type": "mongodb",
            "host": "dds-xxx.mongodb.rds.aliyuncs.com",
            "port": 27017,
            "user": "nocode_mongo",
            "database": "nocode_docs",
            "source_env_key": "MONGO_HOST",
            "source_type": "configmap",
            "source_name": "nocode-config",
        }
    ],
    "service_dependencies": [
        {
            "target_service": "auth-service",
            "target_namespace": "auth",
            "protocol": "http",  # or "grpc"
            "source_env_key": "AUTH_SERVICE_HOST",
            "discovered_url": "auth-service.auth.svc.cluster.local",
        }
    ],
    "external_dependencies": [
        {
            "name": "Alibaba OSS",
            "url": "nocode-bucket.oss-ap-southeast-1.aliyuncs.com",
            "category": "alibaba_cloud",
            "source_env_key": "OSS_ENDPOINT",
        }
    ],
    "cache_layers": [
        {
            "type": "redis",
            "host": "r-xxx.redis.rds.aliyuncs.com",
            "port": 6379,
            "source_env_key": "REDIS_HOST",
        }
    ],
}
```

### 1.9 Service Scanner (`service_scanner.py`)

**Purpose:** Scan K8s Service objects to enrich the dependency map.

**Extract:**
- Service name, namespace, type (ClusterIP, NodePort, LoadBalancer)
- Selectors (which pods it routes to)
- Ports (name, port, targetPort, protocol)
- External IPs or LoadBalancer endpoints (for externally-exposed services)

**Use this to:**
- Validate discovered inter-service dependencies (does the target service actually exist as a K8s Service?)
- Detect services that are exposed externally (LoadBalancer/NodePort)
- Identify orphan services (K8s Service exists but no matching deployment)

### 1.10 CLI Entry Point (`main.py`)

```
Usage: python main.py [OPTIONS]

Options:
  --context TEXT        Kubeconfig context to use (overrides config)
  --env TEXT            Environment label: "staging" or "production"
  --namespaces TEXT     Comma-separated namespaces to scan (default: all)
  --exclude TEXT        Comma-separated namespaces to exclude
  --output-dir TEXT     Output directory (default: ./output)
  --format TEXT         Output format: "xlsx" (default), "json", "both"
  --diff               Compare two environments (requires prior scans)
  --verbose             Enable debug logging
```

**Execution flow:**

1. Load kubeconfig with specified context
2. Discover all namespaces (or use specified list), exclude system namespaces
3. For each namespace:
   a. Scan deployments → extract env vars, container metadata
   b. Scan ConfigMaps → resolve env var references
   c. Scan Secrets → metadata only, flag dependency indicators
   d. Scan Services → validate and enrich
4. Run env_parser on all collected env vars
5. Run db_detector, service_dep_detector, external_dep_detector
6. Build ServiceProfile objects
7. Export to Excel
8. Print summary to console

---

## Phase 2: Data Model

### 2.1 Service Profile Data Class (`models/service_profile.py`)

Use Pydantic models for validation:

```python
class DatabaseConnection(BaseModel):
    db_type: str                    # postgresql, mongodb, redis, mysql, elasticsearch
    host: str
    port: int
    user: Optional[str]
    database_name: Optional[str]
    instance_id: Optional[str]      # Cloud provider instance ID (parsed from hostname)
    source_env_key: str             # Which env var this came from
    source_type: str                # configmap, secret, direct
    source_name: Optional[str]      # Name of the configmap/secret
    environment: str                # staging or production

class ServiceDependency(BaseModel):
    target_service: str
    target_namespace: str
    protocol: str                   # http, grpc, tcp
    source_env_key: str
    discovered_url: str
    validated: bool                 # Whether target exists as a K8s Service

class ExternalDependency(BaseModel):
    name: str
    url: str
    category: str                   # alibaba_cloud, third_party_api, cdn_storage, etc.
    source_env_key: str

class CacheConnection(BaseModel):
    cache_type: str                 # redis, memcached
    host: str
    port: int
    source_env_key: str

class HealthCheck(BaseModel):
    check_type: str                 # liveness, readiness, startup
    path: Optional[str]             # HTTP path
    port: int
    scheme: str                     # HTTP, TCP, exec

class OperationalInfo(BaseModel):
    """Empty by default — for manual enrichment"""
    team_owner: Optional[str] = None
    team_slack_channel: Optional[str] = None
    on_call_rotation: Optional[str] = None
    runbook_url: Optional[str] = None
    sla_target: Optional[str] = None
    alerting_channel: Optional[str] = None
    repo_url: Optional[str] = None
    notes: Optional[str] = None

class ServiceProfile(BaseModel):
    name: str
    namespace: str
    environment: str
    replicas: int
    images: list[str]
    labels: dict[str, str]
    annotations: dict[str, str]
    cpu_request: Optional[str]
    cpu_limit: Optional[str]
    memory_request: Optional[str]
    memory_limit: Optional[str]
    ports: list[int]
    databases: list[DatabaseConnection]
    service_dependencies: list[ServiceDependency]
    external_dependencies: list[ExternalDependency]
    cache_connections: list[CacheConnection]
    health_checks: list[HealthCheck]
    operational_info: OperationalInfo
    raw_env_count: int              # Total number of env vars
    unclassified_env_keys: list[str] # Env vars we couldn't classify
```

### 2.2 Environment Differ (`analyzers/env_differ.py`)

**Purpose:** Compare the same service across staging and production to detect drift.

**Detect and flag:**
- Services that exist in one environment but not the other
- DB connections that differ (different hosts, different instance IDs)
- Service dependencies that differ
- Replica count differences
- Image tag differences (different versions deployed)
- Resource limit differences

**Output:** A list of `EnvironmentDiff` objects that get their own sheet in the Excel output.

---

## Phase 3: Excel Output

### 3.1 Workbook Structure

The `.xlsx` workbook contains **6 sheets**:

#### Sheet 1: "Services Overview"

Master list — one row per service per environment.

| Column | Description | Example |
|---|---|---|
| Service Name | Deployment name | `nocode-service` |
| Namespace | K8s namespace | `nocode` |
| Environment | staging / production | `production` |
| Replicas | Desired replica count | `3` |
| Image | Container image:tag | `registry.cn/nocode:v2.1.0` |
| CPU Request | | `250m` |
| CPU Limit | | `1000m` |
| Memory Request | | `512Mi` |
| Memory Limit | | `2Gi` |
| # DBs | Count of database connections | `2` |
| # Service Deps | Count of inter-service deps | `3` |
| # External Deps | Count of external deps | `1` |
| Has Cache | Yes/No | `Yes` |
| Health Endpoint | Readiness probe path | `/healthz` |
| Team Owner | *(empty — manual fill)* | |
| Notes | *(empty — manual fill)* | |

**Formatting:**
- Header row: bold, dark blue background (#1F4E79), white text, frozen
- Auto-filter enabled on all columns
- Conditional formatting: highlight rows where `# DBs = 0` in yellow (unusual)
- Column widths: auto-fit based on content
- Font: Arial 10pt

#### Sheet 2: "Database Connections"

One row per database connection per service.

| Column | Description | Example |
|---|---|---|
| Service Name | | `nocode-service` |
| Namespace | | `nocode` |
| Environment | | `production` |
| DB Type | postgresql/mongodb/redis/mysql/elasticsearch | `postgresql` |
| Host | DB hostname | `rm-xxx.pg.rds.aliyuncs.com` |
| Port | | `5432` |
| Instance ID | Parsed from Alibaba RDS hostname | `rm-xxx` |
| User | DB username | `nocode_user` |
| Database Name | | `nocode_db` |
| Source Env Key | Which env var held this | `PG_HOST` |
| Source Type | configmap / secret / direct | `configmap` |
| Source Name | ConfigMap or Secret name | `nocode-config` |

**Formatting:**
- Color-code rows by DB Type (light blue for PG, light green for Mongo, light orange for Redis, etc.)
- Group by Service Name with collapsible rows

#### Sheet 3: "Service Dependencies"

One row per inter-service dependency.

| Column | Description | Example |
|---|---|---|
| Source Service | The service that calls | `nocode-service` |
| Source Namespace | | `nocode` |
| Target Service | The service being called | `auth-service` |
| Target Namespace | | `auth` |
| Environment | | `production` |
| Protocol | http / grpc / tcp | `http` |
| Source Env Key | Which env var held this | `AUTH_SERVICE_HOST` |
| Discovered URL | Full URL found | `auth-service.auth.svc.cluster.local` |
| Target Exists | Whether target K8s Service exists | `Yes` |

**Formatting:**
- Conditional formatting: red highlight if `Target Exists = No` (broken dependency!)
- Sort by Source Service by default

#### Sheet 4: "External Dependencies"

One row per external dependency.

| Column | Description | Example |
|---|---|---|
| Service Name | | `nocode-service` |
| Namespace | | `nocode` |
| Environment | | `production` |
| Dependency Name | Auto-detected or hostname | `Alibaba OSS` |
| URL/Endpoint | | `nocode-bucket.oss-ap-southeast-1.aliyuncs.com` |
| Category | alibaba_cloud / third_party / cdn / etc. | `alibaba_cloud` |
| Source Env Key | | `OSS_ENDPOINT` |

#### Sheet 5: "Environment Diff"

One row per detected difference between staging and production.

| Column | Description | Example |
|---|---|---|
| Service Name | | `nocode-service` |
| Namespace | | `nocode` |
| Diff Type | missing_service / db_host_diff / replica_diff / image_diff / dep_diff | `image_diff` |
| Staging Value | | `registry.cn/nocode:v2.0.9` |
| Production Value | | `registry.cn/nocode:v2.1.0` |
| Severity | info / warning / critical | `info` |
| Notes | | `Different image versions` |

**Formatting:**
- Color-code by Severity: yellow (info), orange (warning), red (critical)
- `missing_service` diffs are always `critical`
- `db_host_diff` diffs are `warning` (may be intentional for staging/prod separation, but worth flagging)

#### Sheet 6: "Operations"

One row per service — mostly empty, for manual enrichment by the team.

| Column | Description | Auto-filled? |
|---|---|---|
| Service Name | | Yes |
| Namespace | | Yes |
| Team Owner | | **No** |
| Team Slack Channel | | **No** |
| On-Call Rotation | | **No** |
| Runbook URL | | **No** |
| SLA Target | | **No** |
| Alerting Channel | | **No** |
| Repo URL | | **No** |
| Notes | | **No** |

**Formatting:**
- Yellow background on all empty cells to indicate "needs filling"
- Data validation dropdown for common teams (if team list is provided)

### 3.2 Excel Export Implementation Notes

- Use `openpyxl` (not pandas ExcelWriter — we need fine-grained formatting control)
- Apply the xlsx skill best practices: Arial font, zero formula errors, professional formatting
- Every sheet gets: frozen header row, auto-filter, auto-fit column widths
- Add a "Generated" timestamp and environment label in cell A1 of each sheet (as a note/comment, not a row)
- Protect the auto-filled columns from accidental editing (optional, via sheet protection with password)

---

## Execution Order for the Implementing Model

### Step 1: Set up project skeleton
Create the directory structure from §1.1 and `requirements.txt` from §1.2.

### Step 2: Implement data models
Build `models/service_profile.py` with all Pydantic models from §2.1. This is the foundation — everything else depends on these types.

### Step 3: Implement K8s client wrapper
Build `discovery/k8s_client.py` from §1.4. This needs to be testable without a live cluster (provide a mock/dry-run mode that reads from JSON fixtures).

### Step 4: Implement scanners
Build in this order:
1. `deployment_scanner.py` (§1.5) — most important, extracts env vars
2. `configmap_scanner.py` (§1.6) — resolves env var references
3. `secret_scanner.py` (§1.7) — metadata only
4. `service_scanner.py` (§1.9) — validates dependencies

### Step 5: Implement env parser and detectors
Build `env_parser.py` (§1.8) — the core intelligence. Then build the analyzers:
1. `db_detector.py` — uses DB_PATTERNS from §1.8
2. `service_dep_detector.py` — uses KUBE_DNS_PATTERNS from §1.8
3. `external_dep_detector.py` — uses EXTERNAL_PATTERNS from §1.8

### Step 6: Implement environment differ
Build `env_differ.py` (§2.2).

### Step 7: Implement Excel exporter
Build `excel_exporter.py` following the sheet specs from §3.1 and §3.2. Use the xlsx skill for best practices.

### Step 8: Implement CLI entry point
Build `main.py` (§1.10) — wire everything together.

### Step 9: Add JSON fixture mode
Create a `--dry-run` flag that loads sample K8s API responses from JSON files instead of a live cluster. Include 3-5 sample services with varied configurations for testing.

### Step 10: Test with sample data
Generate a sample `.xlsx` using the JSON fixtures to verify all sheets, formatting, and logic work correctly before running against a live cluster.

---

## Notes for the Implementing Model

1. **Security first:** Never read or log actual Secret values. Only use Secret key names for dependency detection.

2. **Alibaba Cloud specifics:** Alibaba RDS hostnames follow patterns like `rm-xxxxx.pg.rds.aliyuncs.com` (PostgreSQL), `dds-xxxxx.mongodb.rds.aliyuncs.com` (MongoDB), `r-xxxxx.redis.rds.aliyuncs.com` (Redis). Parse the instance ID prefix (e.g., `rm-xxxxx`) for the Excel output.

3. **ConfigMap/Secret resolution:** Many env vars in deployments use `valueFrom.configMapKeyRef` or `valueFrom.secretKeyRef`. The scanner MUST resolve these references to get the actual hostname values. Without this, you'll miss most dependencies.

4. **Env var naming is inconsistent:** Across 80+ services, expect no consistent naming convention. The regex patterns must be broad. When in doubt, flag an env var as "unclassified" rather than silently ignoring it — the unclassified list helps the team identify gaps.

5. **Multiple containers per pod:** Some deployments have sidecar containers. Scan ALL containers in a pod spec, but label which container each env var came from.

6. **The Operations sheet is intentionally empty.** Its purpose is to give the team a structured place to manually add ownership, runbooks, and SLAs. Pre-populate only the service name and namespace.

7. **Output file naming:** `service_discovery_{env}_{YYYY-MM-DD}.xlsx` — e.g., `service_discovery_production_2026-04-30.xlsx`. This allows historical comparison.

8. **Performance:** 80+ services with their ConfigMaps and Secrets could mean 500+ API calls. Use async where possible, and implement a progress bar with `rich`.
