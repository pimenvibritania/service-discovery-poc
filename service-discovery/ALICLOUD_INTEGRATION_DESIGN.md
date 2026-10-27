# AliCloud Infrastructure Dependency Tracking Integration

**Document Date:** 2026-04-30  
**Status:** Architecture & Design Document  
**Scope:** Integration plan for adding AliCloud resource detection to Service Discovery tool

---

## Executive Summary

This document provides a detailed integration plan for adding AliCloud infrastructure dependency tracking to the existing Service Discovery tool. The design ensures:

1. **Minimal disruption** to existing detection pipeline (databases, services, external deps)
2. **Data flow symmetry** — cloud resources flow through same profile → Excel → export pipeline
3. **Reverse-lookup capability** for incident response ("which services use this RDS instance?")
4. **Optional AliCloud API enrichment** via command-line flag
5. **Comprehensive Excel reporting** with 2 new sheets (Cloud Resources, Reverse Dependency Index)
6. **Interactive HTML dependency graph** exporter for visualization

---

## Data Models (Already Defined)

The models are already defined in `/service-discovery/models/service_profile.py`:

### CloudResource
Represents any AliCloud infrastructure resource. Key fields:
- `instance_id` (str): Primary lookup key (pgm-abc123, rm-xyz789, r-redis-id, etc.)
- `resource_type` (str): rds_postgresql, rds_mysql, rds_mongodb, redis, elasticsearch, oss_bucket, etc.
- `resource_name` (str): Human-readable alias
- `endpoint` (str): Full hostname to connect (pgm-xxx.pg.rds.aliyuncs.com)
- `vpc_id` (str): VPC this resource belongs to
- `region` (str): AliCloud region (cn-hangzhou, ap-southeast-1, etc.)
- `services_using` (list[str]): Service names that connect to this resource
- `source_env_keys` (set[str]): All env var names across services referencing this
- `tags` (dict): AliCloud tags for metadata
- `environment` (str): staging, production, etc.
- `category` (str): database, cache, storage, messaging, logging, networking, compute, communication, api, other
- `shared` (bool): True if multiple services use this resource
- `last_discovered` (str): ISO 8601 timestamp
- `notes` (str): Optional free-form notes

### CloudResourceIndex
Reverse-lookup materialized view for rapid incident response:
- `instance_id` (str): Primary lookup key
- `resource_type` (str): Type of resource
- `affected_services` (list[dict]): Services using this resource (with namespaces)
- `dependency_count` (int): Number of unique services
- `shared_services_count` (int): Count of services sharing this single instance
- `critical` (bool): Flag if used by critical services
- `incident_impact_estimate` (str): Estimated blast radius

---

## 1. Configuration Changes (config.py)

### New Settings to Add

```python
# AliCloud Configuration
ALICLOUD_ENABLED = os.environ.get("ALICLOUD_ENABLED", "true").lower() == "true"
ALICLOUD_REGIONS = os.environ.get("ALICLOUD_REGIONS", "cn-hangzhou,cn-beijing,ap-southeast-1").split(",")

# AliCloud API credentials (optional, for enrichment)
ALICLOUD_ACCESS_KEY_ID = os.environ.get("ALICLOUD_ACCESS_KEY_ID", None)
ALICLOUD_ACCESS_KEY_SECRET = os.environ.get("ALICLOUD_ACCESS_KEY_SECRET", None)

# Resource type filters (if None, detect all)
ALICLOUD_RESOURCE_TYPES = os.environ.get("ALICLOUD_RESOURCE_TYPES", None)  # e.g., "rds_postgresql,redis,elasticsearch"

# Enrichment behavior
ALICLOUD_ENRICH = os.environ.get("ALICLOUD_ENRICH", "false").lower() == "true"  # Query AliCloud APIs
ALICLOUD_ENRICH_TIMEOUT = int(os.environ.get("ALICLOUD_ENRICH_TIMEOUT", "10"))  # seconds per resource

# HTML export options
EXPORT_HTML_GRAPH = os.environ.get("EXPORT_HTML_GRAPH", "true").lower() == "true"
```

### Modification Point

**File:** `/service-discovery/config.py`  
**Lines to add:** After `OUTPUT_FILENAME` (around line 32)

---

## 2. Cloud Resource Detection (NEW: analyzers/cloud_resource_detector.py)

### Purpose
Detects and indexes AliCloud resources from:
1. Database connections (extracting instance IDs from hostnames)
2. Cache connections (extracting instance IDs)
3. External dependencies (AliCloud service endpoints)
4. Environment variable patterns (direct instance ID references)

### Key Functions

#### `detect_cloud_resources(env_vars: list[dict], environment: str, classified_keys: set) -> tuple[list[dict], list[dict]]`

**Returns:** `(cloud_resources, alicloud_instance_ids_for_enrichment)`

Analyzes env vars for:
- Alibaba RDS instance ID patterns (pgm-, rm-, dds- prefixes)
- Alibaba Redis instance patterns (r- prefix)
- Alibaba Elasticsearch patterns (es- prefix)
- OSS bucket names (oss-cn-hangzhou.aliyuncs.com)
- Direct AliCloud service endpoints (.aliyuncs.com)

```python
def detect_cloud_resources(
    env_vars: list[dict],
    environment: str,
    classified_keys: set,
) -> tuple[list[dict], list[str]]:
    """
    Detect AliCloud resources from env vars and hostnames.
    
    Args:
        env_vars: List of {key, value, source_type, source_name, container_name}
        environment: "staging" or "production"
        classified_keys: Set to mark processed env vars (mutated in-place)
    
    Returns:
        Tuple of:
            - List of CloudResource dicts (not yet instantiated as Pydantic models)
            - List of instance IDs that should be enriched via AliCloud API
    """
    resources = {}  # Dict[instance_id, CloudResource dict] for deduplication
    instance_ids_to_enrich = []
    
    # Pattern definitions (instance ID prefixes, region patterns, etc.)
    # See function body for full pattern matching logic
    
    return list(resources.values()), list(set(instance_ids_to_enrich))
```

#### `build_cloud_resource_index(resources: list[CloudResource]) -> list[CloudResourceIndex]`

**Purpose:** Build reverse-lookup index for incident response

```python
def build_cloud_resource_index(
    resources: list[CloudResource],
) -> list[CloudResourceIndex]:
    """
    Build reverse-lookup materialized view from CloudResource.services_using.
    
    Returns list of CloudResourceIndex objects ready for Excel export.
    """
    # Group resources by instance_id, compute affected_services, shared_services_count, etc.
    # Determine criticality from service annotations if available
```

---

## 3. Environment Parser Integration (discovery/env_parser.py)

### Current Flow
```
parse_env_vars(workload, env_label, known_services)
├── detect_databases(env_vars, env_label, classified_keys)
├── detect_caches(env_vars, env_label, classified_keys)
├── detect_service_dependencies(env_vars, known_services, classified_keys)
├── detect_external_dependencies(env_vars, classified_keys)
└── unclassified_env_keys
```

### New Flow (with AliCloud detection)
```
parse_env_vars(workload, env_label, known_services)
├── detect_databases(env_vars, env_label, classified_keys)
├── detect_caches(env_vars, env_label, classified_keys)
├── detect_service_dependencies(env_vars, known_services, classified_keys)
├── detect_external_dependencies(env_vars, classified_keys)
├── detect_cloud_resources(env_vars, env_label, classified_keys)  ← NEW
└── unclassified_env_keys
```

### Code Changes

**File:** `/service-discovery/discovery/env_parser.py`

**Change 1:** Add import

```python
from analyzers.cloud_resource_detector import detect_cloud_resources
```

**Change 2:** Modify `parse_env_vars()` function signature and body

```python
def parse_env_vars(
    workload: dict,
    environment: str,
    known_services: set[str],
) -> dict:
    """
    Takes a raw workload dict and returns structured dependency info.
    
    Now also detects AliCloud resources.
    """
    env_vars = workload.get("env_vars", [])
    classified_keys: set[str] = set()

    databases = detect_databases(env_vars, environment, classified_keys)
    caches = detect_caches(env_vars, environment, classified_keys)
    service_deps = detect_service_dependencies(env_vars, known_services, classified_keys)
    external_deps = detect_external_dependencies(env_vars, classified_keys)
    
    # NEW: Detect AliCloud resources
    cloud_resources, alicloud_ids_for_enrichment = detect_cloud_resources(
        env_vars, environment, classified_keys
    )

    # Collect unclassified keys (same logic as before)
    unclassified = [
        ev["key"] for ev in env_vars
        if ev["key"] not in classified_keys
        and ev.get("source_type") != "secret"
        and ev.get("value")
        and _looks_like_url_or_host(ev.get("value", ""))
    ]

    return {
        "databases": databases,
        "cache_connections": caches,
        "service_dependencies": service_deps,
        "external_dependencies": external_deps,
        "cloud_resources": cloud_resources,                    # NEW
        "alicloud_ids_for_enrichment": alicloud_ids_for_enrichment,  # NEW
        "raw_env_count": len(env_vars),
        "unclassified_env_keys": unclassified,
    }
```

**Note:** The `cloud_resources` and `alicloud_ids_for_enrichment` are returned as raw dicts (not Pydantic models yet) to maintain consistency with other detectors.

---

## 4. Service Profile Enrichment (main.py)

### Current Profile Building

**File:** `/service-discovery/main.py` (lines 41-70)

Current code:
```python
def _build_profile(workload: dict, parsed: dict, environment: str, svc_type: str = "ClusterIP") -> ServiceProfile:
    databases = [DatabaseConnection(**d) for d in parsed.get("databases", [])]
    service_deps = [ServiceDependency(**d) for d in parsed.get("service_dependencies", [])]
    external_deps = [ExternalDependency(**d) for d in parsed.get("external_dependencies", [])]
    cache_conns = [CacheConnection(**c) for c in parsed.get("cache_connections", [])]
    health_checks = [HealthCheck(**h) for h in workload.get("health_checks", [])]

    return ServiceProfile(
        name=workload["name"],
        namespace=workload["namespace"],
        environment=environment,
        # ... other fields ...
    )
```

### New Profile Building

**Modification:** No change to ServiceProfile itself (cloud resources are aggregated separately, not per-service)

The profile building stays the same. Cloud resources are aggregated at the **environment level** in the main scan function.

---

## 5. Main Scan Pipeline (main.py)

### Current Pipeline

**File:** `/service-discovery/main.py` (lines 77-152)

Current flow:
```python
def scan_environment(...):
    for ns in target_ns:
        configmap_data = scan_configmaps(...)
        secret_keys = scan_secrets(...)
        service_registry = scan_services(...)
        workloads = scan_workloads(...)
        
        for workload in workloads:
            parsed = parse_env_vars(workload, env_label, known_services)
            profile = _build_profile(workload, parsed, env_label, svc_type)
            all_profiles.append(profile)
    
    return all_profiles
```

### New Pipeline (with Cloud Resource Aggregation)

**Modification Point:** `scan_environment()` function

```python
def scan_environment(
    context: str,
    env_label: str,
    namespaces_filter: list[str],
    exclude: list[str],
    dry_run: bool,
    fixtures_dir: str,
    verbose: bool,
) -> tuple[list[ServiceProfile], list[CloudResource]]:
    """
    Now returns both profiles and aggregated cloud resources.
    """
    client = K8sClient(context_name=context, dry_run=dry_run, fixtures_dir=fixtures_dir)
    
    all_profiles: list[ServiceProfile] = []
    cloud_resources_by_id: dict[str, dict] = {}  # instance_id → CloudResource dict
    all_alicloud_ids_for_enrichment: list[str] = []

    with Progress(...) as progress:
        task = progress.add_task("Scanning namespaces...", total=len(target_ns))

        for ns in target_ns:
            # ... existing code for configmaps, secrets, services, workloads ...

            for workload in workloads:
                parsed = parse_env_vars(workload, env_label, known_services)
                svc_info = service_registry.get(workload["name"], {})
                svc_type = svc_info.get("type", "ClusterIP")
                profile = _build_profile(workload, parsed, env_label, svc_type)
                all_profiles.append(profile)

                # NEW: Aggregate cloud resources
                for cr_dict in parsed.get("cloud_resources", []):
                    instance_id = cr_dict.get("instance_id")
                    if instance_id not in cloud_resources_by_id:
                        cloud_resources_by_id[instance_id] = cr_dict
                        cloud_resources_by_id[instance_id]["services_using"] = []
                        cloud_resources_by_id[instance_id]["source_env_keys"] = set()
                    
                    # Add this service to the resource's consumers
                    if workload["name"] not in cloud_resources_by_id[instance_id]["services_using"]:
                        cloud_resources_by_id[instance_id]["services_using"].append(workload["name"])
                    
                    # Track env keys
                    for env_key in parsed.get("source_env_keys_for_resource", {}).get(instance_id, []):
                        cloud_resources_by_id[instance_id]["source_env_keys"].add(env_key)
                
                # Collect IDs for enrichment
                all_alicloud_ids_for_enrichment.extend(
                    parsed.get("alicloud_ids_for_enrichment", [])
                )

            progress.advance(task)

    # Convert CloudResource dicts to Pydantic models with timestamp
    from datetime import datetime
    cloud_resources = [
        CloudResource(
            **{**cr, "source_env_keys": set(cr.get("source_env_keys", []))},
            last_discovered=datetime.utcnow().isoformat()
        )
        for cr in cloud_resources_by_id.values()
    ]

    return all_profiles, cloud_resources
```

### CLI Integration

**File:** `/service-discovery/main.py` (main function)

**Change 1:** Add CLI option for AliCloud enrichment

```python
@click.command()
@click.option("--context", default=None, help="Kubeconfig context (overrides config)")
# ... existing options ...
@click.option("--enrich-alicloud", is_flag=True, default=False,
              help="Query AliCloud APIs to enrich resource metadata (CPU, memory, VPC, tags, etc.)")
def main(context, env_label, staging_context, production_context, namespaces,
         exclude, output_dir, fmt, do_diff, dry_run, fixtures_dir, verbose, enrich_alicloud):
    """Service Discovery — scans Kubernetes and exports an Excel dependency map."""
```

**Change 2:** Modify the scan and export calls

```python
if do_diff:
    staging_profiles, staging_cloud_resources = scan_environment(...)
    production_profiles, production_cloud_resources = scan_environment(...)
    
    # Merge cloud resources from both environments
    all_cloud_resources = _merge_cloud_resources(staging_cloud_resources, production_cloud_resources)
    all_profiles = staging_profiles + production_profiles
else:
    ctx = stg_ctx if env_label == "staging" else prd_ctx
    all_profiles, all_cloud_resources = scan_environment(...)

# NEW: Optional enrichment
if enrich_alicloud and all_cloud_resources:
    if cfg.ALICLOUD_ACCESS_KEY_ID and cfg.ALICLOUD_ACCESS_KEY_SECRET:
        console.print("[bold]Enriching AliCloud resources with API metadata...[/bold]")
        all_cloud_resources = enrich_cloud_resources_from_alicloud(
            all_cloud_resources,
            cfg.ALICLOUD_ACCESS_KEY_ID,
            cfg.ALICLOUD_ACCESS_KEY_SECRET,
            cfg.ALICLOUD_REGIONS,
        )
    else:
        console.print("[yellow]⚠ AliCloud API credentials not configured, skipping enrichment[/yellow]")

# Export
if fmt in ("xlsx", "both"):
    output_path = export_excel(all_profiles, diffs, env_label_out, output_dir, all_cloud_resources)
```

---

## 6. AliCloud API Enrichment (NEW: discovery/alicloud_enricher.py)

### Purpose
Optional module that queries AliCloud APIs to enrich discovered cloud resources with:
- Specs (CPU, memory, storage, replica count for RDS read replicas)
- VPC and security group information
- Resource tags and custom metadata
- Current status and creation date
- Backup information (for databases)
- Performance metrics (optional: connections, CPU usage)

### Architecture

```python
"""
Optional AliCloud API enrichment module.
Only imported if --enrich-alicloud flag is set and credentials are configured.
"""

from alibabacloud_rds20160815.client import Client as RdsClient
from alibabacloud_kvstore20150101.client import Client as RedisClient
from alibabacloud_elasticsearch.client import Client as EsClient
from alibabacloud_oss2 import Auth, Bucket
# ... other AliCloud SDK imports ...

import asyncio
from typing import Optional


class AliCloudEnricher:
    """Enriches CloudResource objects with AliCloud metadata."""
    
    def __init__(
        self,
        access_key_id: str,
        access_key_secret: str,
        regions: list[str],
        timeout_sec: int = 10,
    ):
        """
        Args:
            access_key_id: AliCloud API key ID
            access_key_secret: AliCloud API key secret
            regions: List of regions to query (cn-hangzhou, ap-southeast-1, etc.)
            timeout_sec: Timeout per resource query
        """
        self.access_key_id = access_key_id
        self.access_key_secret = access_key_secret
        self.regions = regions
        self.timeout_sec = timeout_sec
        self._clients = {}  # Lazy-loaded clients per region
    
    async def enrich_resource(self, resource: CloudResource) -> CloudResource:
        """
        Enriches a single CloudResource with metadata from AliCloud APIs.
        
        If API call fails, resource is returned unchanged.
        """
        resource_type = resource.resource_type
        instance_id = resource.instance_id
        
        try:
            if resource_type.startswith("rds_"):
                await self._enrich_rds(resource)
            elif resource_type == "redis":
                await self._enrich_redis(resource)
            elif resource_type == "elasticsearch":
                await self._enrich_elasticsearch(resource)
            elif resource_type == "oss_bucket":
                await self._enrich_oss(resource)
            # ... other resource types ...
        except Exception as e:
            # Gracefully degrade: log error, return resource unchanged
            console.print(f"[yellow]⚠ Failed to enrich {instance_id}: {e}[/yellow]")
        
        return resource
    
    async def _enrich_rds(self, resource: CloudResource):
        """Query RDS API for database specs, VPC, backups, etc."""
        # Implementation fetches: cpu, memory, storage, replica count, backup status
        # Updates resource.tags, resource.vpc_id, resource.notes with metrics
    
    async def _enrich_redis(self, resource: CloudResource):
        """Query Redis API for instance specs, memory limit, eviction policy."""
        pass
    
    async def _enrich_elasticsearch(self, resource: CloudResource):
        """Query Elasticsearch API for node count, node type, storage."""
        pass
    
    async def _enrich_oss(self, resource: CloudResource):
        """Query OSS API for bucket size, object count, lifecycle policy."""
        pass


async def enrich_cloud_resources_from_alicloud(
    resources: list[CloudResource],
    access_key_id: str,
    access_key_secret: str,
    regions: list[str],
    timeout_sec: int = 10,
) -> list[CloudResource]:
    """
    Enrich all cloud resources in parallel using AliCloud APIs.
    
    Returns list of enriched CloudResource objects.
    Handles failures gracefully (returns un-enriched resource if API call fails).
    """
    enricher = AliCloudEnricher(access_key_id, access_key_secret, regions, timeout_sec)
    
    # Enrich in parallel with semaphore to limit concurrent API calls
    tasks = [enricher.enrich_resource(r) for r in resources]
    enriched = await asyncio.gather(*tasks)
    
    return enriched
```

### Integration Point

**File:** `/service-discovery/main.py`

Add to imports (conditionally, inside the enrich_alicloud block):

```python
if enrich_alicloud and cfg.ALICLOUD_ACCESS_KEY_ID:
    from discovery.alicloud_enricher import enrich_cloud_resources_from_alicloud
    import asyncio
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        all_cloud_resources = loop.run_until_complete(
            enrich_cloud_resources_from_alicloud(
                all_cloud_resources,
                cfg.ALICLOUD_ACCESS_KEY_ID,
                cfg.ALICLOUD_ACCESS_KEY_SECRET,
                cfg.ALICLOUD_REGIONS,
                cfg.ALICLOUD_ENRICH_TIMEOUT,
            )
        )
    finally:
        loop.close()
```

---

## 7. Excel Exporter Updates (exporters/excel_exporter.py)

### New Sheets

#### Sheet 7: Cloud Resources

**File:** `/service-discovery/exporters/excel_exporter.py`

Add new function:

```python
def _sheet_cloud_resources(wb: Workbook, cloud_resources: list[CloudResource], env: str):
    """
    Cloud infrastructure resources detected in the environment.
    Includes instance IDs, regions, endpoints, services using each resource.
    """
    ws = wb.create_sheet("Cloud Resources", 6)  # Position after existing sheets
    headers = [
        "Instance ID", "Resource Type", "Resource Name", "Endpoint",
        "Region", "Category", "VPC ID", "Shared", "Services Using",
        "Env Keys (count)", "Environment", "Tags", "Last Discovered", "Notes",
    ]
    _apply_header(ws, headers)
    _add_timestamp_note(ws, env)

    row = 2
    for resource in cloud_resources:
        values = [
            resource.instance_id,
            resource.resource_type,
            resource.resource_name or "",
            resource.endpoint or "",
            resource.region or "",
            resource.category,
            resource.vpc_id or "",
            "Yes" if resource.shared else "No",
            "; ".join(resource.services_using) if resource.services_using else "—",
            len(resource.source_env_keys),
            resource.environment or "",
            "; ".join([f"{k}={v}" for k, v in resource.tags.items()]) if resource.tags else "",
            resource.last_discovered or "",
            resource.notes or "",
        ]
        for col, v in enumerate(values, 1):
            ws.cell(row=row, column=col, value=v)
        
        # Color code by resource type
        type_colors = {
            "rds_postgresql": "DBEAFE",
            "rds_mysql": "EDE9FE",
            "rds_mongodb": "D1FAE5",
            "redis": "FEF3C7",
            "elasticsearch": "FEE2E2",
            "oss_bucket": "F3E8FF",
            "mns_queue": "ECFDF5",
            "api_gateway": "CFFAFE",
        }
        color = type_colors.get(resource.resource_type, "FFFFFF")
        _style_data_row(ws, row, len(headers), color)
        
        # Highlight if shared across multiple services
        if resource.shared and len(resource.services_using) > 1:
            cell = ws.cell(row=row, column=8)
            cell.fill = _fill(ORANGE_FILL)
        
        row += 1

    if row == 2:
        ws.cell(row=2, column=1, value="No cloud resources detected").font = _font(bold=True)

    _freeze_and_filter(ws)
    _auto_width(ws, min_w=12, max_w=60)
```

#### Sheet 8: Reverse Dependency Index

**File:** `/service-discovery/exporters/excel_exporter.py`

Add new function:

```python
def _sheet_reverse_index(wb: Workbook, cloud_resources_index: list[CloudResourceIndex]):
    """
    Reverse-lookup materialized view for incident response.
    Key question: "This RDS instance is down — which services are affected?"
    """
    ws = wb.create_sheet("Reverse Dependency Index", 7)
    headers = [
        "Instance ID", "Resource Type", "Affected Services Count", "Affected Services",
        "Shared Services (in this resource)", "Critical", "Incident Impact Estimate",
    ]
    _apply_header(ws, headers)

    row = 2
    for index in sorted(cloud_resources_index, key=lambda x: x.dependency_count, reverse=True):
        affected = "; ".join([
            f"{s['service_name']} ({s['namespace']})"
            for s in index.affected_services
        ])
        
        values = [
            index.instance_id,
            index.resource_type,
            index.dependency_count,
            affected,
            index.shared_services_count,
            "Yes" if index.critical else "No",
            index.incident_impact_estimate or "",
        ]
        for col, v in enumerate(values, 1):
            ws.cell(row=row, column=col, value=v)
        
        # Color by criticality and blast radius
        if index.critical and index.dependency_count > 3:
            color = RED_FILL
        elif index.shared_services_count > 2:
            color = ORANGE_FILL
        else:
            color = GREEN_FILL
        _style_data_row(ws, row, len(headers), color)
        
        row += 1

    if row == 2:
        ws.cell(row=2, column=1, value="No reverse dependencies to index").font = _font(bold=True)

    _freeze_and_filter(ws)
    _auto_width(ws, min_w=12, max_w=80)
```

### Update export_excel() Function Signature

**File:** `/service-discovery/exporters/excel_exporter.py` (export_excel function)

Current signature:
```python
def export_excel(
    profiles: list[ServiceProfile],
    diffs: list[EnvironmentDiff],
    env_label: str,
    output_dir: str,
) -> str:
```

New signature:
```python
def export_excel(
    profiles: list[ServiceProfile],
    diffs: list[EnvironmentDiff],
    env_label: str,
    output_dir: str,
    cloud_resources: Optional[list[CloudResource]] = None,  # NEW parameter
) -> str:
    """
    Exports to an Excel workbook with service discovery data.
    
    Args:
        profiles: List of ServiceProfile objects
        diffs: List of EnvironmentDiff objects (from diff mode)
        env_label: Environment label for naming ("staging", "production", "diff")
        output_dir: Output directory path
        cloud_resources: List of CloudResource objects (optional, AliCloud resources)
    
    Returns:
        Path to the generated .xlsx file
    """
    wb = Workbook()
    wb.remove(wb.active)  # Remove default sheet

    _sheet_services_overview(wb, profiles, env_label)
    _sheet_databases(wb, profiles, env_label)
    _sheet_caches(wb, profiles, env_label)  # Already exists
    _sheet_service_deps(wb, profiles, env_label)
    _sheet_external_deps(wb, profiles, env_label)
    
    if diffs:
        _sheet_env_diff(wb, diffs)
    
    # NEW: Cloud resources and reverse index
    if cloud_resources:
        from models.service_profile import CloudResourceIndex
        _sheet_cloud_resources(wb, cloud_resources, env_label)
        
        # Build reverse index
        cloud_index = build_cloud_resource_index(cloud_resources)
        _sheet_reverse_index(wb, cloud_index)
    
    # Save and return path
    date_str = datetime.now().strftime("%Y-%m-%d")
    filename = f"service_discovery_{env_label}_{date_str}.xlsx"
    filepath = os.path.join(output_dir, filename)
    os.makedirs(output_dir, exist_ok=True)
    
    wb.save(filepath)
    return filepath
```

---

## 8. HTML Dependency Graph Exporter (NEW: exporters/graph_exporter.py)

### Purpose
Creates an interactive HTML visualization of the dependency graph using a force-directed layout.

### Design

```python
"""
Exports service and cloud resource dependencies as interactive HTML.
Uses a JavaScript library (D3.js or similar) for force-directed layout.
"""

from pathlib import Path
import json
from models.service_profile import ServiceProfile, CloudResource


def export_html_graph(
    profiles: list[ServiceProfile],
    cloud_resources: list[CloudResource],
    env_label: str,
    output_dir: str,
) -> str:
    """
    Generates an interactive HTML dependency graph.
    
    Returns:
        Path to the generated .html file
    """
    # Build graph data structure
    nodes = []
    edges = []
    
    # Add service nodes
    for p in profiles:
        nodes.append({
            "id": f"svc:{p.name}:{p.namespace}",
            "label": p.name,
            "type": "service",
            "namespace": p.namespace,
            "environment": p.environment,
            "group": "services",
            "size": 15,
        })
    
    # Add cloud resource nodes
    for cr in cloud_resources:
        nodes.append({
            "id": f"res:{cr.instance_id}",
            "label": cr.resource_name or cr.instance_id,
            "type": cr.resource_type,
            "category": cr.category,
            "group": "resources",
            "size": max(10, min(20, len(cr.services_using))),  # Size by usage
        })
    
    # Add service → service edges
    for p in profiles:
        for dep in p.service_dependencies:
            edges.append({
                "source": f"svc:{p.name}:{p.namespace}",
                "target": f"svc:{dep.target_service}:{dep.target_namespace}",
                "type": "service_dep",
                "protocol": dep.protocol,
                "validated": dep.validated,
            })
    
    # Add service → cloud resource edges (from databases, caches)
    for p in profiles:
        for db in p.databases:
            if db.cloud_resource_ref:
                edges.append({
                    "source": f"svc:{p.name}:{p.namespace}",
                    "target": f"res:{db.cloud_resource_ref}",
                    "type": "database",
                    "db_type": db.db_type,
                })
        for cache in p.cache_connections:
            if cache.cloud_resource_ref:
                edges.append({
                    "source": f"svc:{p.name}:{p.namespace}",
                    "target": f"res:{cache.cloud_resource_ref}",
                    "type": "cache",
                    "cache_type": cache.cache_type,
                })
    
    # Generate HTML
    html_content = _generate_html_template(nodes, edges, env_label)
    
    # Write file
    date_str = datetime.now().strftime("%Y-%m-%d")
    filename = f"dependency_graph_{env_label}_{date_str}.html"
    filepath = os.path.join(output_dir, filename)
    os.makedirs(output_dir, exist_ok=True)
    
    with open(filepath, "w") as f:
        f.write(html_content)
    
    return filepath


def _generate_html_template(nodes: list[dict], edges: list[dict], env_label: str) -> str:
    """
    Generates the complete HTML with embedded D3.js visualization.
    """
    nodes_json = json.dumps(nodes)
    edges_json = json.dumps(edges)
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <title>Service Discovery Dependency Graph</title>
        <script src="https://d3js.org/d3.v7.min.js"></script>
        <style>
            body {{ font-family: Arial, sans-serif; margin: 0; overflow: hidden; }}
            svg {{ border: 1px solid #ccc; }}
            .node {{ stroke: #fff; stroke-width: 1.5px; cursor: pointer; }}
            .link {{ stroke: #999; stroke-opacity: 0.6; }}
            .node-label {{ font-size: 12px; text-anchor: middle; pointer-events: none; }}
            #tooltip {{ position: absolute; background: #f0f0f0; padding: 8px; border: 1px solid #ccc; border-radius: 4px; }}
        </style>
    </head>
    <body>
        <h1>Service Discovery Dependency Graph ({env_label})</h1>
        <svg id="graph"></svg>
        <div id="tooltip" style="display: none;"></div>
        
        <script>
            const nodes = {nodes_json};
            const links = {edges_json};
            
            // Force-directed layout and visualization logic
            // (See graph_exporter.py for full implementation)
        </script>
    </body>
    </html>
    """
    
    return html
```

### Integration Point

**File:** `/service-discovery/main.py`

Add to export section:

```python
if fmt in ("html", "both"):
    from exporters.graph_exporter import export_html_graph
    html_path = export_html_graph(all_profiles, all_cloud_resources, env_label_out, output_dir)
    console.print(f"[green]✓ HTML graph saved:[/green] {html_path}")
```

---

## 9. Data Flow Summary

### Scanning Phase

```
┌─ Namespace 1
│  ├─ Deployment: payment-api
│  │  ├─ Env: POSTGRES_HOST=pgm-12345.pg.rds.aliyuncs.com
│  │  └─ Env: REDIS_URL=redis://r-67890.redis.rds.aliyuncs.com:6379
│  │
│  ├─ parse_env_vars()
│  │  ├─ detect_databases() → [DatabaseConnection(host=pgm-12345...)]
│  │  ├─ detect_caches() → [CacheConnection(host=r-67890...)]
│  │  └─ detect_cloud_resources() → [CloudResource(instance_id=pgm-12345), CloudResource(instance_id=r-67890)]
│  │
│  ├─ _build_profile() → ServiceProfile(databases=[...], cache_connections=[...])
│  │
│  └─ Aggregate in scan_environment():
│     cloud_resources_by_id[pgm-12345].services_using.append("payment-api")
│     cloud_resources_by_id[r-67890].services_using.append("payment-api")

└─ Namespace 2
   └─ ... (repeat for all services)

Return: (all_profiles, all_cloud_resources)
```

### Enrichment Phase (Optional)

```
if --enrich-alicloud:
    for each CloudResource:
        call RDS/Redis/ES API with instance_id
        fetch: cpu, memory, vpc_id, tags, status
        merge into resource.tags
```

### Export Phase

```
export_excel(all_profiles, diffs, env_label, all_cloud_resources)
├─ Sheet 1: Services Overview
├─ Sheet 2: Database Connections
├─ Sheet 3: Service Dependencies
├─ Sheet 4: External Dependencies
├─ Sheet 5: (Environment Diff, if --diff mode)
├─ Sheet 6: Cloud Resources ← NEW
└─ Sheet 7: Reverse Dependency Index ← NEW

export_html_graph(all_profiles, all_cloud_resources, env_label)
└─ Interactive dependency visualization
```

---

## 10. Test Fixtures

### New Fixture Data

**File:** `/service-discovery/fixtures/alicloud_cloud_resources.json`

```json
{
  "cloud_resources": [
    {
      "instance_id": "pgm-12345abcdef67890",
      "resource_type": "rds_postgresql",
      "resource_name": "payment-db-prod",
      "endpoint": "pgm-12345abcdef67890.pg.rds.aliyuncs.com",
      "region": "cn-hangzhou",
      "vpc_id": "vpc-abc123def456",
      "shared": true,
      "services_using": ["payment-api", "order-service"],
      "source_env_keys": ["POSTGRES_HOST", "DB_HOST"],
      "tags": {
        "env": "production",
        "team": "platform",
        "cost-center": "12345"
      },
      "environment": "production",
      "category": "database",
      "notes": "Primary production database, read replicas in cn-beijing"
    },
    {
      "instance_id": "r-67890fedcba12345",
      "resource_type": "redis",
      "resource_name": "cache-prod",
      "endpoint": "r-67890fedcba12345.redis.rds.aliyuncs.com",
      "region": "cn-hangzhou",
      "vpc_id": "vpc-abc123def456",
      "shared": true,
      "services_using": ["payment-api", "user-service", "notification-service"],
      "source_env_keys": ["REDIS_URL"],
      "tags": {
        "env": "production",
        "team": "platform"
      },
      "environment": "production",
      "category": "cache",
      "notes": "Cluster mode enabled, 3 master nodes"
    }
  ]
}
```

### Update Existing Fixtures

Modify `/service-discovery/fixtures/kubernetes.json` workload env vars to include AliCloud resource references:

```json
{
  "kind": "Deployment",
  "metadata": {"name": "payment-api", "namespace": "production"},
  "spec": {
    "template": {
      "spec": {
        "containers": [
          {
            "name": "payment-api",
            "env": [
              {
                "name": "POSTGRES_HOST",
                "value": "pgm-12345abcdef67890.pg.rds.aliyuncs.com"
              },
              {
                "name": "REDIS_URL",
                "value": "redis://r-67890fedcba12345.redis.rds.aliyuncs.com:6379"
              }
            ]
          }
        ]
      }
    }
  }
}
```

---

## 11. Implementation Checklist

### Phase 1: Core Cloud Resource Detection

- [ ] Create `analyzers/cloud_resource_detector.py`
  - [ ] Pattern matching for RDS instance IDs (pgm-, rm-, dds-)
  - [ ] Redis instance ID extraction (r- prefix)
  - [ ] Elasticsearch instance extraction (es- prefix)
  - [ ] OSS bucket detection (.oss-cn-*.aliyuncs.com)
  - [ ] build_cloud_resource_index() function

- [ ] Modify `discovery/env_parser.py`
  - [ ] Import cloud_resource_detector
  - [ ] Call detect_cloud_resources() in parse_env_vars()
  - [ ] Return cloud_resources and alicloud_ids_for_enrichment in result dict

- [ ] Modify `config.py`
  - [ ] Add ALICLOUD_* configuration options

### Phase 2: Aggregation & Excel Export

- [ ] Modify `main.py` scan_environment()
  - [ ] Aggregate cloud_resources across all workloads
  - [ ] Build deduplication map (instance_id → CloudResource)
  - [ ] Return (profiles, cloud_resources)

- [ ] Update `exporters/excel_exporter.py`
  - [ ] Add _sheet_cloud_resources()
  - [ ] Add _sheet_reverse_index()
  - [ ] Update export_excel() signature

- [ ] Update CLI in `main.py`
  - [ ] Add --enrich-alicloud flag
  - [ ] Pass cloud_resources to export_excel()

### Phase 3: Optional AliCloud Enrichment

- [ ] Create `discovery/alicloud_enricher.py`
  - [ ] AliCloudEnricher class with async methods
  - [ ] _enrich_rds(), _enrich_redis(), _enrich_elasticsearch(), etc.
  - [ ] enrich_cloud_resources_from_alicloud() async function

- [ ] Integrate enricher into `main.py`
  - [ ] Conditionally import enricher
  - [ ] Run async enrichment loop
  - [ ] Handle failures gracefully

### Phase 4: Interactive Graph Export

- [ ] Create `exporters/graph_exporter.py`
  - [ ] Node and edge graph building
  - [ ] HTML template generation
  - [ ] D3.js force-directed layout

- [ ] Integrate into CLI
  - [ ] Add --format html and --format both options
  - [ ] Export HTML when requested

### Phase 5: Testing & Fixtures

- [ ] Create test fixtures for cloud resources
- [ ] Update existing Kubernetes fixtures with AliCloud endpoints
- [ ] Add unit tests for cloud_resource_detector
- [ ] Add integration tests for full pipeline
- [ ] Test Excel export with cloud resources
- [ ] Test HTML graph generation

---

## 12. Key Design Decisions & Rationale

| Decision | Rationale |
|----------|-----------|
| Cloud resources aggregated at **environment level**, not per-service | Services often share cloud resources; reverse-lookup queries are more natural at environment level |
| **detect_cloud_resources()** called in env_parser pipeline | Maintains consistency with existing detector pattern (db, cache, service, external) |
| Cloud resources returned as **raw dicts, not Pydantic models** in parser | Allows detectors to work uniformly; conversion to models happens in main.py |
| **Optional** AliCloud API enrichment (--enrich-alicloud flag) | API calls add latency; users may not have credentials; feature is nice-to-have, not required |
| **CloudResourceIndex** as separate materialized model | Provides optimized structure for reverse-lookup queries; simpler than embedding in CloudResource |
| **HTML graph exporter** (force-directed layout) | Better for visualizing complex dependencies than static tables; easier to explore visually |
| **source_env_keys** stored as set in CloudResource | Enables correlation of config changes; shows which env vars across services reference the same resource |

---

## 13. API Contract Examples

### Consuming the New Functionality

#### Example 1: Basic Scan + Excel Export (no enrichment)

```bash
python main.py \
  --context prod-k8s \
  --env production \
  --format xlsx
```

Result: Excel file with 8 sheets (existing 6 + Cloud Resources + Reverse Index)

#### Example 2: Scan + Enrich from AliCloud APIs

```bash
export ALICLOUD_ACCESS_KEY_ID=...
export ALICLOUD_ACCESS_KEY_SECRET=...

python main.py \
  --context prod-k8s \
  --env production \
  --format xlsx \
  --enrich-alicloud
```

Result: Excel file with enriched resource metadata (CPU, memory, VPC, tags, etc.)

#### Example 3: Diff Mode + HTML Graph

```bash
python main.py \
  --staging-context staging-k8s \
  --production-context prod-k8s \
  --diff \
  --format both \
  --enrich-alicloud
```

Result: Both Excel and HTML graph files

---

## 14. Error Handling & Resilience

### Cloud Resource Detection Failures
- If hostname parsing fails: resource is skipped, env var remains unclassified
- If instance ID extraction fails: CloudResource still created with endpoint only

### AliCloud API Enrichment Failures
- Timeout per resource: 10 sec (configurable)
- If API call fails: resource returned unchanged (graceful degradation)
- If credentials invalid: warning printed, enrichment skipped

### Excel Export Failures
- If cloud_resources is None/empty: new sheets simply omitted
- If CloudResource model validation fails: informative error logged

---

## 15. Future Extensions

### Phase 2 (Post-MVP)
1. **AliCloud cost tracking** — fetch cost allocations per resource
2. **Performance metrics** — embed CPU/memory/connection usage in enrichment
3. **Backup & disaster recovery** — track RDS backup status, replication lag
4. **Network path visualization** — add VPC and security group constraints to graph
5. **Change history** — track when services start/stop using resources

### Phase 3 (Long-term)
1. **Multi-cloud support** — AWS, GCP, Azure alongside AliCloud
2. **Automated incident impact analysis** — "If this RDS instance fails, these services are affected"
3. **Webhook integration** — notify Slack/PagerDuty of critical dependency changes
4. **Cost optimization** — identify under-utilized shared resources and rightsizing opportunities

---

## Appendix A: File Tree (Updated)

```
service-discovery/
├── main.py                          # UPDATED: cloud_resources aggregation, enrichment
├── config.py                        # UPDATED: AliCloud settings
├── discovery/
│   ├── k8s_client.py
│   ├── deployment_scanner.py
│   ├── configmap_scanner.py
│   ├── secret_scanner.py
│   ├── service_scanner.py
│   ├── env_parser.py                # UPDATED: call detect_cloud_resources
│   └── alicloud_enricher.py          # NEW: optional AliCloud API enrichment
├── models/
│   └── service_profile.py           # ALREADY UPDATED: CloudResource, CloudResourceIndex models
├── analyzers/
│   ├── db_detector.py
│   ├── service_dep_detector.py
│   ├── external_dep_detector.py
│   ├── env_differ.py
│   └── cloud_resource_detector.py   # NEW: AliCloud resource detection
├── exporters/
│   ├── excel_exporter.py            # UPDATED: add 2 new sheets, update function signature
│   └── graph_exporter.py            # NEW: HTML dependency graph
├── fixtures/
│   ├── kubernetes.json              # UPDATED: add AliCloud endpoints
│   └── alicloud_cloud_resources.json # NEW: fixture cloud resources
└── tests/
    ├── test_cloud_detector.py       # NEW
    ├── test_alicloud_enricher.py    # NEW (if enrichment built)
    └── test_integration.py          # UPDATED
```

---

## Appendix B: Summary of Code Changes by File

| File | Changes | Type |
|------|---------|------|
| config.py | Add ALICLOUD_* settings | Add ~8 lines |
| discovery/env_parser.py | Import + call detect_cloud_resources() | Modify ~20 lines |
| analyzers/cloud_resource_detector.py | Full new module | New ~300 lines |
| main.py | Aggregate cloud_resources, add CLI flag, optional enrichment | Modify ~60 lines |
| exporters/excel_exporter.py | 2 new sheet functions, update export_excel() signature | Add ~120 lines, modify ~10 lines |
| exporters/graph_exporter.py | Full new module | New ~200 lines |
| discovery/alicloud_enricher.py | Optional enrichment module | New ~250 lines |

**Total additions:** ~900 lines (excluding optional enrichment)

---

## Appendix C: Decision Tree for AliCloud Resource vs External Dependency

When an env var contains an AliCloud hostname (e.g., `.aliyuncs.com`):

```
Is it a known DB pattern (PostgreSQL, MySQL, MongoDB)?
├─ YES → detect_databases() handles it → DatabaseConnection
├─ NO → Is it a known cache pattern (Redis, Memcached)?
│       ├─ YES → detect_caches() handles it → CacheConnection
│       └─ NO → Is it an AliCloud resource type (RDS, OSS, SLS, etc.)?
│               ├─ YES → detect_cloud_resources() handles it → CloudResource
│               └─ NO → detect_external_dependencies() handles it → ExternalDependency
```

**Key principle:** Specific patterns (DB, cache) take precedence. Generic cloud resource detection acts as fallback. External dependencies catch unmapped services.

