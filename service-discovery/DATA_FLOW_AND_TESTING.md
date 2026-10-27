# Data Flow Diagrams & Testing Strategy

---

## 1. High-Level Data Flow

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                          Service Discovery Pipeline                             │
│                                                                                 │
│  Input: Kubernetes Cluster (env vars in Deployments, ConfigMaps, Secrets)      │
│                                                                                 │
└──────────────────────────────┬──────────────────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────────────────────┐
│                      SCANNING & DETECTION PHASE                                  │
│                                                                                  │
│  For each namespace (filter + exclude):                                         │
│    1. Scan ConfigMaps (for env var references)                                  │
│    2. Scan Secrets (metadata only, security)                                    │
│    3. Scan K8s Services (for dep validation)                                    │
│    4. Scan Deployments/StatefulSets                                             │
│       ├─ Workload struct: {name, namespace, env_vars, resources, health_checks} │
│       └─ env_vars: [{key, value, source_type, source_name, container_name}]    │
│                                                                                  │
│  For each workload, call parse_env_vars(workload, env_label, known_services):   │
│                                                                                  │
│    Parallel detection pipeline (all classifiers run on same env_vars):          │
│    ├─ detect_databases() → DatabaseConnection[] (existing)                      │
│    ├─ detect_caches() → CacheConnection[] (existing)                            │
│    ├─ detect_service_dependencies() → ServiceDependency[] (existing)            │
│    ├─ detect_external_dependencies() → ExternalDependency[] (existing)          │
│    └─ detect_cloud_resources() → CloudResource[] (NEW)                          │
│                                                                                  │
│  Return: ParsedDeps = {                                                         │
│    databases: [...],                                                            │
│    cache_connections: [...],                                                    │
│    service_dependencies: [...],                                                 │
│    external_dependencies: [...],                                                │
│    cloud_resources: [...],                           # NEW                      │
│    alicloud_ids_for_enrichment: [instance_id, ...], # NEW                       │
│    unclassified_env_keys: [...]                                                 │
│  }                                                                               │
│                                                                                  │
└──────────────────────────────┬──────────────────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────────────────────┐
│                    PROFILE & AGGREGATION PHASE                                   │
│                                                                                  │
│  For each workload:                                                             │
│    1. _build_profile(workload, parsed_deps) → ServiceProfile                   │
│       └─ Converts dicts to Pydantic models (Database, Cache, ServiceDep, etc.)  │
│    2. Append to all_profiles[]                                                  │
│                                                                                  │
│  NEW: Aggregate cloud resources (deduplication by instance_id):                │
│    For each cr_dict in parsed_deps["cloud_resources"]:                          │
│      if instance_id not in cloud_resources_by_id:                              │
│        create new entry                                                        │
│      add service name to services_using[]                                       │
│      add env_keys to source_env_keys set                                        │
│                                                                                  │
│  Convert aggregated cloud_resources dicts to Pydantic CloudResource models:     │
│    ├─ Compute derived fields: shared (len(services_using) > 1)                  │
│    ├─ Set last_discovered timestamp                                             │
│    └─ Handle validation errors gracefully                                       │
│                                                                                  │
│  Return: (all_profiles[], all_cloud_resources[])                                │
│                                                                                  │
└──────────────────────────────┬──────────────────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────────────────────┐
│                 OPTIONAL: ALICLOUD ENRICHMENT PHASE                              │
│                          (if --enrich-alicloud)                                  │
│                                                                                  │
│  Prerequisites:                                                                  │
│    ✓ ALICLOUD_ACCESS_KEY_ID env var set                                        │
│    ✓ ALICLOUD_ACCESS_KEY_SECRET env var set                                    │
│                                                                                  │
│  Process:                                                                        │
│    For each cloud_resource.instance_id in alicloud_ids_for_enrichment:         │
│      1. Determine resource region from endpoint domain                          │
│      2. Select appropriate AliCloud client (RDS, Redis, ES, OSS, etc.)         │
│      3. Call API: get resource metadata                                         │
│      4. Merge into resource.tags and resource.notes:                            │
│         ├─ CPU, memory, storage specs                                          │
│         ├─ VPC ID and security groups                                          │
│         ├─ Replica status (for RDS)                                            │
│         ├─ Tags and cost allocations                                           │
│         ├─ Backup status (for databases)                                       │
│         └─ Creation date, status                                               │
│      5. Handle API failures gracefully (return un-enriched resource)           │
│                                                                                  │
│  Parallelism: Up to 5 concurrent API calls (configurable semaphore)            │
│  Timeout: 10 seconds per resource (configurable)                                │
│                                                                                  │
│  Return: enriched all_cloud_resources[]                                         │
│                                                                                  │
│  Note: Cloud resource instances may span staging AND production                │
│        (merged by _merge_cloud_resources in diff mode)                          │
│                                                                                  │
└──────────────────────────────┬──────────────────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────────────────────┐
│                      EXCEL EXPORT PHASE                                          │
│                                                                                  │
│  Call: export_excel(all_profiles, diffs, env_label, output_dir,               │
│                     cloud_resources=all_cloud_resources)                        │
│                                                                                  │
│  Sheet structure (8 sheets total):                                              │
│    ├─ Sheet 1: Services Overview                                               │
│    │   Columns: Service, Namespace, Env, Replicas, Image, CPU, Memory,         │
│    │            # DBs, # Service Deps, # External Deps, Has Cache, ...         │
│    │   Rows: one per service                                                    │
│    │   Purpose: High-level summary of all services and their dependencies      │
│    │                                                                             │
│    ├─ Sheet 2: Database Connections (existing)                                 │
│    │   Columns: Service, Namespace, DB Type, Host, Port, Instance ID, User,    │
│    │            Database Name, Source Env Key, Source Type, Source Name        │
│    │   Rows: one per database connection                                        │
│    │   Purpose: Detailed database connectivity mapping                         │
│    │                                                                             │
│    ├─ Sheet 3: Service Dependencies (existing)                                 │
│    │   Columns: Source Service, Source Namespace, Target Service,              │
│    │            Target Namespace, Environment, Protocol, Source Env Key,       │
│    │            Discovered URL, Target Exists                                   │
│    │   Rows: one per inter-service dependency                                   │
│    │   Purpose: Map K8s service-to-service communication                       │
│    │   Coloring: Red if target service doesn't exist                           │
│    │                                                                             │
│    ├─ Sheet 4: External Dependencies (existing)                                │
│    │   Columns: Service, Namespace, Env, Dependency Name, URL/Endpoint,        │
│    │            Category, Source Env Key                                        │
│    │   Rows: one per external API/service call                                  │
│    │   Purpose: Track third-party integrations (Stripe, Twilio, etc.)         │
│    │                                                                             │
│    ├─ Sheet 5: Environment Diff (if --diff mode)                               │
│    │   Columns: Service, Namespace, Diff Type, Staging Value,                  │
│    │            Production Value, Severity, Notes                               │
│    │   Rows: one per detected difference                                        │
│    │   Coloring: Red (critical), Orange (warning), White (info)                │
│    │   Purpose: Compare staging vs production configurations                    │
│    │                                                                             │
│    ├─ Sheet 6: Cloud Resources (NEW)                                           │
│    │   Columns: Instance ID, Resource Type, Resource Name, Endpoint,           │
│    │            Region, Category, VPC ID, Shared, # Services Using,            │
│    │            Services Using, Env Keys, Environment, Tags,                   │
│    │            Last Discovered, Notes                                         │
│    │   Rows: one per unique AliCloud resource                                   │
│    │   Coloring: By resource type (PostgreSQL blue, MySQL purple, Redis gold,  │
│    │            Elasticsearch red, etc.)                                        │
│    │   Purpose: Inventory all AliCloud infrastructure consumed by services    │
│    │   Key field: "Services Using" shows which services depend on each resource │
│    │                                                                             │
│    └─ Sheet 7: Reverse Dependency Index (NEW)                                  │
│        Columns: Instance ID, Resource Type, # Services Affected,               │
│                 Affected Services, Shared (in this resource),                   │
│                 Critical, Incident Impact Estimate                              │
│        Rows: one per unique cloud resource, sorted by dependency_count DESC    │
│        Coloring: Red (critical + high impact), Orange (shared by multiple),    │
│                  Green (isolated)                                               │
│        Purpose: FOR INCIDENT RESPONSE                                           │
│                 Answer: "This RDS instance is down — which services?"          │
│        Key insight: Materialized reverse-lookup for fast queries                │
│                                                                                  │
│  Output: service_discovery_{env}_{YYYY-MM-DD}.xlsx                             │
│                                                                                  │
└──────────────────────────────┬──────────────────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────────────────────┐
│                 OPTIONAL: HTML GRAPH EXPORT                                      │
│                        (if --format html)                                        │
│                                                                                  │
│  Build graph data structure:                                                    │
│    Nodes:                                                                        │
│      ├─ Service nodes (blue circles, size 15)                                  │
│      └─ Cloud resource nodes (colored by type, size based on services_using)   │
│                                                                                  │
│    Edges:                                                                        │
│      ├─ Service → Service (validated = green solid, broken = red dashed)      │
│      ├─ Service → Database (blue solid)                                        │
│      └─ Service → Cache (gold solid)                                           │
│                                                                                  │
│  Render:                                                                         │
│    ├─ D3.js force-directed layout (simulate physics, repulsion, attraction)    │
│    ├─ Draggable nodes                                                          │
│    ├─ Click node to see detailed info panel                                    │
│    ├─ Legend and statistics                                                    │
│    └─ Zoom and pan interaction                                                 │
│                                                                                  │
│  Output: dependency_graph_{env}_{YYYY-MM-DD}.html                              │
│                                                                                  │
└──────────────────────────────┬──────────────────────────────────────────────────┘
                               │
                               ▼
                        FINAL OUTPUTS:
                        ├─ Excel workbook (8 sheets)
                        ├─ HTML dependency graph (optional)
                        └─ JSON export (optional)
```

---

## 2. Cloud Resource Detection Flow Detail

```
Input: Single env var = {key, value, source_type, source_name, container_name}
Examples:
  ├─ key=POSTGRES_HOST, value=pgm-12345.pg.rds.cn-hangzhou.aliyuncs.com
  ├─ key=REDIS_URL, value=redis://r-67890.redis.rds.cn-hangzhou.aliyuncs.com:6379
  ├─ key=ELASTICSEARCH_HOST, value=es-abcde.es.cn-hangzhou.aliyuncs.com
  └─ key=OSS_BUCKET, value=my-bucket.oss-cn-hangzhou.aliyuncs.com

┌───────────────────────────────────────────────────────────────┐
│ detect_cloud_resources(env_vars, environment, classified_keys) │
└────────────────────────────┬────────────────────────────────────┘
                             │
         ┌───────────────────┴────────────────────┐
         │                                        │
         ▼                                        ▼
  For each env_var:              For each pattern in ALICLOUD_PATTERNS:
  ├─ Check if already classified  ├─ Pattern: rds_postgresql
  │  (by other detectors)         │   Prefixes: [pgm-]
  │                               │   Domains: [.pg.rds.aliyuncs.com]
  ├─ Check if value contains      │   Resource Type: rds_postgresql
  │  .aliyuncs.com (AliCloud)     │
  │                               ├─ Pattern: redis
  ├─ Identify resource type       │   Prefixes: [r-]
  │  (call _identify_resource_    │   Domains: [.redis.rds.aliyuncs.com]
  │   type which loops patterns)  │   Resource Type: redis
  │                               │
  ├─ Extract instance ID          └─ Pattern: oss_bucket
  │  from hostname prefix            Prefixes: None (match by domain only)
  │                                  Domains: [.oss-]
  ├─ Detect region from             Resource Type: oss_bucket
  │  endpoint (cn-hangzhou, ...)
  │
  └─ Deduplicate by instance_id   Match? ─────┐
     (track services_using and        Yes      │
     source_env_keys)                 ▼        │
                                   Found!      │
                                   ├─ Instance ID: pgm-12345
                                   ├─ Resource Type: rds_postgresql
                                   ├─ Endpoint: pgm-12345.pg.rds...
                                   ├─ Region: cn-hangzhou (parsed)
                                   ├─ Category: database
                                   └─ services_using: [payment-api]
                                      source_env_keys: [POSTGRES_HOST]

Result:
  ├─ List of CloudResource dicts (not yet Pydantic):
  │  [{instance_id, resource_type, endpoint, region, category, ...}]
  │
  └─ List of instance IDs for optional API enrichment:
     [pgm-12345, r-67890, es-abcde]
```

---

## 3. Cloud Resource Aggregation Flow

```
Scenario: 3 services (payment-api, user-service, order-service)
All use the same PostgreSQL and Redis instances

Step 1: First service (payment-api)
  Input: parsed["cloud_resources"] from parse_env_vars(payment-api)
    ├─ {instance_id: pgm-12345, resource_type: rds_postgresql, ...}
    └─ {instance_id: r-67890, resource_type: redis, ...}
  
  Aggregation:
    cloud_resources_by_id[pgm-12345] = {..., services_using: [payment-api]}
    cloud_resources_by_id[r-67890] = {..., services_using: [payment-api]}

Step 2: Second service (user-service)
  Input: parsed["cloud_resources"] from parse_env_vars(user-service)
    └─ {instance_id: r-67890, resource_type: redis, ...}  # Same Redis!
  
  Aggregation:
    cloud_resources_by_id[r-67890].services_using += [user-service]
    Result: services_using: [payment-api, user-service]

Step 3: Third service (order-service)
  Input: parsed["cloud_resources"] from parse_env_vars(order-service)
    ├─ {instance_id: pgm-12345, resource_type: rds_postgresql, ...}  # Same DB!
    └─ {instance_id: r-67890, resource_type: redis, ...}  # Same Redis!
  
  Aggregation:
    cloud_resources_by_id[pgm-12345].services_using += [order-service]
    cloud_resources_by_id[r-67890].services_using += [order-service]
    Results:
      pgm-12345: services_using: [payment-api, order-service], shared: true
      r-67890: services_using: [payment-api, user-service, order-service], shared: true

Final aggregated resources:
  ├─ pgm-12345: rds_postgresql, shared by [payment-api, order-service]
  └─ r-67890: redis, shared by [payment-api, user-service, order-service]
```

---

## 4. Reverse Dependency Index (for Incident Response)

```
Input: all_cloud_resources[] (after aggregation)

Example queries to answer:

Q: "PostgreSQL pgm-12345 is down. Which services are affected?"
A: (Look up "Reverse Dependency Index" sheet)
   Row with instance_id=pgm-12345
   Read affected_services: [payment-api (production), order-service (production)]
   Incident impact estimate: "Payment processing blocked, ~5000 txns/hour affected"

Q: "We're optimizing costs. Which resources are used by only one service?"
A: Filter rows where shared_services_count = 1
   Results: Can be consolidated/shared

Q: "Which AliCloud resources are critical?"
A: Filter rows where critical = True
   Results: [pgm-12345, r-67890, es-abcde]
   These should have better SLAs, monitoring, disaster recovery

Materialized structure (one row per resource):
┌─────────────────┬──────────────────┬──────────────────┬──────────────────────────┐
│ instance_id     │ resource_type    │ affected_services│ incident_impact_estimate │
├─────────────────┼──────────────────┼──────────────────┼──────────────────────────┤
│ pgm-12345       │ rds_postgresql   │ 2                │ Payment processing      │
│                 │                  │ payment-api      │ blocked, ~5000 txns/hr  │
│                 │                  │ order-service    │                         │
├─────────────────┼──────────────────┼──────────────────┼──────────────────────────┤
│ r-67890         │ redis            │ 3                │ Session cache lost,     │
│                 │                  │ payment-api      │ users forced to re-login│
│                 │                  │ user-service     │                         │
│                 │                  │ notification-svc │                         │
├─────────────────┼──────────────────┼──────────────────┼──────────────────────────┤
│ es-abcde        │ elasticsearch    │ 1                │ Search API returns 503, │
│                 │                  │ search-service   │ minor user impact       │
└─────────────────┴──────────────────┴──────────────────┴──────────────────────────┘

Derived from cloud_resources[]:
  - affected_services = cloud_resources.services_using
  - shared_services_count = len(cloud_resources.services_using)
  - critical = (shared_services_count > 3) OR (resource_type in [rds_*, elasticsearch])
```

---

## 5. Testing Strategy

### 5.1 Unit Tests

#### Test: cloud_resource_detector.py

```python
# tests/test_cloud_resource_detector.py

def test_extract_instance_id_rds_postgresql():
    """Extract pgm-xxx from PostgreSQL endpoint."""
    result = _extract_instance_id(
        "pgm-12345abc.pg.rds.cn-hangzhou.aliyuncs.com",
        ["pgm-"]
    )
    assert result == "pgm-12345abc"

def test_detect_region_from_endpoint():
    """Parse region from AliCloud endpoint domain."""
    result = _detect_region_from_endpoint(
        "pgm-12345.pg.rds.cn-hangzhou.aliyuncs.com"
    )
    assert result == "cn-hangzhou"

def test_detect_cloud_resources_postgres():
    """Detect PostgreSQL instance from env vars."""
    env_vars = [
        {
            "key": "POSTGRES_HOST",
            "value": "pgm-12345.pg.rds.cn-hangzhou.aliyuncs.com",
            "source_type": "configmap",
            "source_name": "db-config",
            "container_name": "app",
        }
    ]
    classified_keys = set()
    
    resources, ids_for_enrichment = detect_cloud_resources(
        env_vars, "production", classified_keys
    )
    
    assert len(resources) == 1
    assert resources[0]["instance_id"] == "pgm-12345"
    assert resources[0]["resource_type"] == "rds_postgresql"
    assert resources[0]["region"] == "cn-hangzhou"
    assert "POSTGRES_HOST" in classified_keys

def test_detect_cloud_resources_redis():
    """Detect Redis instance."""
    env_vars = [
        {
            "key": "REDIS_URL",
            "value": "redis://r-67890.redis.rds.ap-southeast-1.aliyuncs.com:6379",
            "source_type": "secret",
            "source_name": "redis-secret",
            "container_name": "app",
        }
    ]
    classified_keys = set()
    
    resources, ids = detect_cloud_resources(env_vars, "staging", classified_keys)
    
    # Secret values are skipped
    assert len(resources) == 0
    assert "REDIS_URL" not in classified_keys

def test_detect_cloud_resources_deduplication():
    """Multiple services referencing same resource."""
    env_vars = [
        {"key": "DB_HOST", "value": "pgm-123.pg.rds.aliyuncs.com", ...},
        {"key": "PG_HOST", "value": "pgm-123.pg.rds.aliyuncs.com", ...},  # Same host!
    ]
    classified_keys = set()
    
    resources, ids = detect_cloud_resources(env_vars, "prod", classified_keys)
    
    # Should deduplicate to one resource
    assert len(resources) == 1
    # But should track both env keys
    assert len(resources[0]["source_env_keys"]) == 2

def test_build_cloud_resource_index():
    """Build reverse-lookup index."""
    from models.service_profile import CloudResource
    
    resources = [
        CloudResource(
            instance_id="pgm-12345",
            resource_type="rds_postgresql",
            services_using=["payment-api", "order-service"],
            category="database",
        ),
        CloudResource(
            instance_id="r-67890",
            resource_type="redis",
            services_using=["payment-api"],
            category="cache",
        ),
    ]
    
    index = build_cloud_resource_index(resources)
    
    assert len(index) == 2
    assert index[0].dependency_count == 2  # 2 services
    assert index[0].shared_services_count == 2
    assert index[0].critical == True  # Database with 2+ services
    assert index[1].critical == False  # Cache with 1 service
```

#### Test: env_parser.py integration

```python
# tests/test_env_parser.py (add to existing)

def test_parse_env_vars_with_cloud_resources():
    """parse_env_vars now includes cloud resources."""
    workload = {
        "name": "payment-api",
        "namespace": "production",
        "env_vars": [
            {"key": "POSTGRES_HOST", "value": "pgm-123.pg.rds.aliyuncs.com", ...},
            {"key": "REDIS_URL", "value": "redis://r-456.redis.rds.aliyuncs.com:6379", ...},
        ],
    }
    
    result = parse_env_vars(workload, "production", set())
    
    assert "cloud_resources" in result
    assert "alicloud_ids_for_enrichment" in result
    assert len(result["cloud_resources"]) == 2
    assert len(result["alicloud_ids_for_enrichment"]) == 2
```

### 5.2 Integration Tests

#### Test: Full Scan Pipeline

```python
# tests/test_integration_alicloud.py

def test_scan_environment_aggregates_cloud_resources():
    """Cloud resources aggregated correctly across namespaces."""
    # Mock K8sClient to return fixtures
    # Scan staging and production
    # Verify aggregation:
    #   - Same resource instance_id deduped
    #   - services_using populated correctly
    #   - Shared flag set correctly
    pass

def test_export_excel_includes_cloud_sheets():
    """Excel export includes Cloud Resources and Reverse Index sheets."""
    profiles = [...]
    cloud_resources = [...]
    
    output_path = export_excel(
        profiles, [], "staging", "/tmp",
        cloud_resources=cloud_resources
    )
    
    # Verify sheet names
    wb = openpyxl.load_workbook(output_path)
    sheet_names = wb.sheetnames
    assert "Cloud Resources" in sheet_names
    assert "Reverse Dependency Index" in sheet_names
    
    # Verify data in Cloud Resources sheet
    ws = wb["Cloud Resources"]
    assert ws["A2"].value == "pgm-12345"  # First resource instance ID
    
    # Verify data in Reverse Index sheet
    ws = wb["Reverse Dependency Index"]
    assert ws["A2"].value == "pgm-12345"  # Same resource, reverse view

def test_export_html_graph_creates_valid_html():
    """HTML graph exporter produces valid HTML with D3.js."""
    profiles = [...]
    cloud_resources = [...]
    
    output_path = export_html_graph(
        profiles, cloud_resources, "staging", "/tmp"
    )
    
    assert output_path.endswith(".html")
    with open(output_path) as f:
        content = f.read()
    
    assert "d3.js" in content or "d3.v7" in content
    assert "force-directed" in content or "forceSimulation" in content
    assert "graph" in content
```

### 5.3 Fixture Updates

#### New Fixture File: fixtures/alicloud_cloud_resources.json

```json
{
  "cloud_resources": [
    {
      "instance_id": "pgm-yzc8phv8jkx3yz",
      "resource_type": "rds_postgresql",
      "resource_name": "payment-db-prod",
      "endpoint": "pgm-yzc8phv8jkx3yz.pg.rds.cn-hangzhou.aliyuncs.com",
      "region": "cn-hangzhou",
      "vpc_id": "vpc-bp1dkiab8o5i5i3fh",
      "shared": true,
      "services_using": ["payment-api", "order-service"],
      "source_env_keys": ["POSTGRES_HOST", "DB_HOST"],
      "tags": {
        "env": "production",
        "team": "platform",
        "backup": "daily"
      },
      "environment": "production",
      "category": "database",
      "notes": "Read replicas in cn-beijing and ap-southeast-1"
    },
    {
      "instance_id": "r-bp1t3bhwjp7gsi1f",
      "resource_type": "redis",
      "resource_name": "session-cache-prod",
      "endpoint": "r-bp1t3bhwjp7gsi1f.redis.rds.cn-hangzhou.aliyuncs.com",
      "region": "cn-hangzhou",
      "vpc_id": "vpc-bp1dkiab8o5i5i3fh",
      "shared": true,
      "services_using": ["payment-api", "user-service", "notification-service"],
      "source_env_keys": ["REDIS_URL", "CACHE_HOST"],
      "tags": {
        "env": "production",
        "team": "platform",
        "ttl": "3600"
      },
      "environment": "production",
      "category": "cache",
      "notes": "Cluster mode, 3 master nodes, 6 replicas total"
    },
    {
      "instance_id": "es-cn-6ja19oy3p002z",
      "resource_type": "elasticsearch",
      "resource_name": "logs-prod",
      "endpoint": "es-cn-6ja19oy3p002z.elasticsearch.aliyuncs.com",
      "region": "cn-hangzhou",
      "vpc_id": "vpc-bp1dkiab8o5i5i3fh",
      "shared": false,
      "services_using": ["analytics-service"],
      "source_env_keys": ["ELASTICSEARCH_HOST"],
      "tags": {
        "env": "production",
        "team": "data"
      },
      "environment": "production",
      "category": "search",
      "notes": "Warm and cold tiers, 30-day retention"
    }
  ]
}
```

#### Update Fixture: fixtures/kubernetes.json

Add AliCloud endpoints to workload env vars:

```json
{
  "deployments": [
    {
      "apiVersion": "apps/v1",
      "kind": "Deployment",
      "metadata": {
        "name": "payment-api",
        "namespace": "production"
      },
      "spec": {
        "replicas": 3,
        "template": {
          "spec": {
            "containers": [
              {
                "name": "payment-api",
                "image": "myrepo/payment-api:v1.2.3",
                "resources": {
                  "requests": {"cpu": "500m", "memory": "512Mi"},
                  "limits": {"cpu": "1000m", "memory": "1Gi"}
                },
                "env": [
                  {
                    "name": "POSTGRES_HOST",
                    "valueFrom": {"configMapKeyRef": {"name": "db-config", "key": "host"}}
                  },
                  {
                    "name": "POSTGRES_USER",
                    "valueFrom": {"secretKeyRef": {"name": "db-secret", "key": "user"}}
                  },
                  {
                    "name": "REDIS_URL",
                    "valueFrom": {"configMapKeyRef": {"name": "cache-config", "key": "url"}}
                  },
                  {
                    "name": "ELASTICSEARCH_HOST",
                    "value": "es-cn-6ja19oy3p002z.elasticsearch.aliyuncs.com"
                  }
                ]
              }
            ]
          }
        }
      }
    }
  ],
  "configmaps": [
    {
      "metadata": {"name": "db-config", "namespace": "production"},
      "data": {
        "host": "pgm-yzc8phv8jkx3yz.pg.rds.cn-hangzhou.aliyuncs.com",
        "port": "5432"
      }
    },
    {
      "metadata": {"name": "cache-config", "namespace": "production"},
      "data": {
        "url": "redis://r-bp1t3bhwjp7gsi1f.redis.rds.cn-hangzhou.aliyuncs.com:6379"
      }
    }
  ],
  "secrets": [
    {
      "metadata": {"name": "db-secret", "namespace": "production"},
      "data": {
        "user": "postgres_admin",
        "password": "..."
      }
    }
  ],
  "services": [
    {
      "metadata": {"name": "payment-api", "namespace": "production"},
      "spec": {
        "type": "ClusterIP",
        "ports": [{"port": 8080, "name": "http"}]
      }
    }
  ]
}
```

### 5.4 Test Command Examples

```bash
# Run all tests
pytest tests/ -v

# Run cloud detector tests only
pytest tests/test_cloud_resource_detector.py -v

# Run integration tests
pytest tests/test_integration_alicloud.py -v

# Run with coverage
pytest tests/ --cov=analyzers --cov=discovery --cov=exporters --cov-report=html

# Run dry-run with test fixtures
python main.py --dry-run --env staging --format xlsx --verbose

# Run with enrichment (requires AliCloud creds)
ALICLOUD_ACCESS_KEY_ID=xxx ALICLOUD_ACCESS_KEY_SECRET=yyy \
python main.py --dry-run --env staging --enrich-alicloud --format xlsx --verbose
```

---

## 6. Validation Checklist

Before deploying to production:

### Code Quality
- [ ] All unit tests pass (pytest)
- [ ] Code coverage > 80% for new modules
- [ ] No linting issues (flake8, mypy)
- [ ] No security issues (bandit)

### Functional
- [ ] Dry-run produces correct Excel with 8 sheets
- [ ] Cloud Resources sheet has all expected columns
- [ ] Reverse Dependency Index shows correct aggregations
- [ ] Shared resources flagged correctly
- [ ] HTML graph renders without JavaScript errors
- [ ] Diff mode merges cloud resources correctly

### Data Quality
- [ ] Instance IDs extracted correctly from all endpoints
- [ ] Regions detected from endpoints
- [ ] Services aggregated without duplicates
- [ ] Deduplication works for same resource across multiple services
- [ ] Unclassified env vars don't include AliCloud resources

### Performance
- [ ] Scan completes in < 2 minutes for typical cluster (100+ services)
- [ ] Excel export < 10 seconds
- [ ] HTML graph < 5 seconds
- [ ] API enrichment parallelizes correctly (semaphore limits concurrent calls)

### Error Handling
- [ ] Graceful degradation if cloud_resources is None/empty
- [ ] API failures don't crash (return un-enriched resource)
- [ ] Malformed endpoints don't crash detector
- [ ] Missing credentials show warning, not error
- [ ] Excel/HTML export handles empty resource lists

### Documentation
- [ ] README updated with --enrich-alicloud flag
- [ ] Example command-lines documented
- [ ] AliCloud credential setup documented
- [ ] Cloud Resources sheet schema documented
- [ ] Reverse Index use cases documented

---

## 7. Rollout Plan

### Phase 1: Development (Week 1-2)
- Implement all modules (code_reference.md order)
- Write unit tests
- Test with fixtures
- Code review

### Phase 2: Beta (Week 3)
- Deploy to internal staging
- Test with real Kubernetes clusters
- Gather feedback on Excel/HTML output
- Performance profiling

### Phase 3: Production Release (Week 4)
- Deploy with feature flag (ALICLOUD_ENABLED=true)
- Monitor logs for errors
- Gather user feedback
- Iterate on UI/output

### Phase 4: AliCloud Enrichment (Weeks 5-6)
- Implement alicloud_enricher.py
- Add to CLI as --enrich-alicloud flag
- Document credential setup
- Beta test with customers

---

## 8. Known Limitations & Future Work

### Limitations (Current MVP)
1. **Namespace mapping in Reverse Index:** affected_services show service name only, not namespace (fixable with additional lookup)
2. **Incident impact estimates:** Currently empty, needs manual enrichment or SLA correlation
3. **Only AliCloud:** No AWS/GCP support (can be extended with cloud-agnostic patterns)
4. **No cost tracking:** Could be added via AliCloud API

### Future Extensions
1. **Multi-cloud support:** AWS RDS, GCP Cloud SQL, etc.
2. **Cost tracking:** Cost per resource and per service
3. **Performance metrics:** CPU, memory, connection pool utilization
4. **Backup monitoring:** RDS backup status, replication lag
5. **Webhook integration:** Slack/PagerDuty notifications on changes
6. **Automated recommendations:** "This resource is under-utilized, consider consolidating"
7. **CMDB sync:** Export to external change management systems
8. **Historical tracking:** Git-based version control of dependency maps

