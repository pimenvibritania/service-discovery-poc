# AliCloud Integration — Code Reference & Implementation Guide

This document provides exact code snippets for each integration point, ready to implement.

---

## 1. config.py — AliCloud Configuration

**File:** `service-discovery/config.py`  
**Action:** Add these lines after line 32 (after `OUTPUT_FILENAME`)

```python
# ─────────────────────────────────────────────────────────────────────────────
# AliCloud Infrastructure Tracking
# ─────────────────────────────────────────────────────────────────────────────

# Enable/disable AliCloud resource detection entirely
ALICLOUD_ENABLED = os.environ.get("ALICLOUD_ENABLED", "true").lower() == "true"

# AliCloud regions to scan (when enriching via API)
ALICLOUD_REGIONS = [
    r.strip() for r in os.environ.get(
        "ALICLOUD_REGIONS",
        "cn-hangzhou,cn-beijing,cn-zhangjiakou,ap-southeast-1,ap-northeast-1,eu-west-1"
    ).split(",")
]

# AliCloud API credentials (optional, only needed for enrichment)
ALICLOUD_ACCESS_KEY_ID = os.environ.get("ALICLOUD_ACCESS_KEY_ID", None)
ALICLOUD_ACCESS_KEY_SECRET = os.environ.get("ALICLOUD_ACCESS_KEY_SECRET", None)

# Resource type filters (None = detect all types)
# Example: "rds_postgresql,rds_mysql,redis,elasticsearch"
ALICLOUD_RESOURCE_TYPES = os.environ.get("ALICLOUD_RESOURCE_TYPES", None)

# Enrichment via API
ALICLOUD_ENRICH = os.environ.get("ALICLOUD_ENRICH", "false").lower() == "true"
ALICLOUD_ENRICH_TIMEOUT = int(os.environ.get("ALICLOUD_ENRICH_TIMEOUT", "10"))

# HTML graph export
EXPORT_HTML_GRAPH = os.environ.get("EXPORT_HTML_GRAPH", "true").lower() == "true"
```

---

## 2. analyzers/cloud_resource_detector.py — NEW FILE

**Action:** Create new file at `service-discovery/analyzers/cloud_resource_detector.py`

```python
"""
Detect AliCloud infrastructure resources from environment variables and hostnames.
Integrates seamlessly with existing db_detector, cache_detector patterns.
"""

import re
from typing import Optional, Callable
from datetime import datetime


# ─────────────────────────────────────────────────────────────────────────────
# AliCloud Instance ID Patterns
# ─────────────────────────────────────────────────────────────────────────────

ALICLOUD_PATTERNS = {
    # RDS Databases
    "rds_postgresql": {
        "prefixes": ["pgm-"],
        "domains": [".pg.rds.aliyuncs.com"],
        "resource_type": "rds_postgresql",
        "category": "database",
    },
    "rds_mysql": {
        "prefixes": ["rm-"],
        "domains": [".mysql.rds.aliyuncs.com"],
        "resource_type": "rds_mysql",
        "category": "database",
    },
    "rds_mongodb": {
        "prefixes": ["dds-"],
        "domains": [".mongodb.rds.aliyuncs.com"],
        "resource_type": "rds_mongodb",
        "category": "database",
    },
    # Cache
    "redis": {
        "prefixes": ["r-"],
        "domains": [".redis.rds.aliyuncs.com"],
        "resource_type": "redis",
        "category": "cache",
    },
    # Search/Analytics
    "elasticsearch": {
        "prefixes": ["es-"],
        "domains": [".es.aliyuncs.com"],
        "resource_type": "elasticsearch",
        "category": "search",
    },
    # Storage
    "oss_bucket": {
        "prefixes": None,  # No prefix, match by domain pattern
        "domains": [".oss-", ".oss."],
        "resource_type": "oss_bucket",
        "category": "storage",
        "hostname_parser": lambda h: h.split(".")[0],  # Extract bucket name
    },
    # Logging
    "sls_log_project": {
        "prefixes": None,
        "domains": [".log.aliyuncs.com"],
        "resource_type": "sls_log_project",
        "category": "logging",
    },
    # Messaging
    "kafka": {
        "prefixes": ["alikafka-"],
        "domains": [".kafka.aliyuncs.com"],
        "resource_type": "kafka_instance",
        "category": "messaging",
    },
    "mns_queue": {
        "prefixes": None,
        "domains": [".mns.aliyuncs.com"],
        "resource_type": "mns_queue",
        "category": "messaging",
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# Helper Functions
# ─────────────────────────────────────────────────────────────────────────────

def _extract_instance_id(hostname: str, prefixes: list[str]) -> Optional[str]:
    """
    Extract AliCloud instance ID from hostname.
    Example: 'pgm-abc123def456.pg.rds.aliyuncs.com' → 'pgm-abc123def456'
    """
    if not hostname or not prefixes:
        return None
    
    parts = hostname.split(".")
    if not parts:
        return None
    
    first_part = parts[0]
    for prefix in prefixes:
        if first_part.startswith(prefix):
            return first_part
    
    return None


def _detect_region_from_endpoint(endpoint: str) -> Optional[str]:
    """
    Infer AliCloud region from endpoint domain.
    Examples:
        pgm-xxx.pg.rds.cn-hangzhou.aliyuncs.com → cn-hangzhou
        r-xxx.redis.rds.ap-southeast-1.aliyuncs.com → ap-southeast-1
    """
    if not endpoint:
        return None
    
    # Pattern: .{region}.aliyuncs.com
    match = re.search(r'\.(cn-[a-z0-9\-]+|ap-[a-z0-9\-]+|eu-[a-z0-9\-]+|us-[a-z0-9\-]+)\.aliyuncs\.com', endpoint)
    if match:
        return match.group(1)
    
    return None


def _matches_domain(hostname: str, domain_patterns: list[str]) -> bool:
    """Check if hostname matches any of the domain patterns."""
    hostname_lower = hostname.lower()
    for pattern in domain_patterns:
        if pattern in hostname_lower:
            return True
    return False


def _identify_resource_type(hostname: str) -> tuple[Optional[str], Optional[dict]]:
    """
    Identify the AliCloud resource type from hostname.
    Returns (resource_type_key, pattern_dict) or (None, None) if no match.
    """
    for resource_key, pattern_info in ALICLOUD_PATTERNS.items():
        prefixes = pattern_info.get("prefixes")
        domains = pattern_info.get("domains", [])
        
        # Check domain match (always required)
        if not _matches_domain(hostname, domains):
            continue
        
        # Check prefix match (if required)
        if prefixes:
            if _extract_instance_id(hostname, prefixes):
                return resource_key, pattern_info
        else:
            # No prefix required (e.g., OSS, SLS)
            return resource_key, pattern_info
    
    return None, None


# ─────────────────────────────────────────────────────────────────────────────
# Main Detection Function
# ─────────────────────────────────────────────────────────────────────────────

def detect_cloud_resources(
    env_vars: list[dict],
    environment: str,
    classified_keys: set,
) -> tuple[list[dict], list[str]]:
    """
    Detect AliCloud resources from environment variables.
    
    Args:
        env_vars: List of {key, value, source_type, source_name, container_name}
        environment: "staging" or "production"
        classified_keys: Set to mark processed env vars (mutated in-place)
    
    Returns:
        Tuple of:
            - List of CloudResource dicts (ready for Pydantic instantiation)
            - List of instance IDs to enrich via AliCloud API (if --enrich-alicloud)
    """
    resources = {}  # Dict[instance_id, CloudResource dict] for deduplication
    instance_ids_for_enrichment = []
    source_env_keys_by_resource = {}  # Dict[instance_id, set[env_key]]

    for ev in env_vars:
        key = ev.get("key", "")
        value = ev.get("value")
        source_type = ev.get("source_type", "direct")
        source_name = ev.get("source_name")
        container_name = ev.get("container_name", "unknown")

        if not value or source_type == "secret":
            continue

        if key in classified_keys:
            continue

        value_str = str(value).strip()

        # Check if this value looks like an AliCloud endpoint
        if not _matches_domain(value_str, [".aliyuncs.com"]):
            continue

        # Try to identify resource type and extract instance ID
        resource_key, pattern_info = _identify_resource_type(value_str)
        
        if not resource_key:
            # Not a recognized AliCloud resource pattern
            continue

        # Extract instance ID (for some resources like OSS, this is the bucket name)
        resource_type = pattern_info.get("resource_type")
        prefixes = pattern_info.get("prefixes", [])
        
        if pattern_info.get("hostname_parser"):
            # Custom parser (e.g., for OSS bucket names)
            instance_id = pattern_info["hostname_parser"](value_str)
        else:
            instance_id = _extract_instance_id(value_str, prefixes)
        
        if not instance_id:
            continue

        # Deduplicate by instance_id
        if instance_id not in resources:
            region = _detect_region_from_endpoint(value_str)
            
            resources[instance_id] = {
                "instance_id": instance_id,
                "resource_type": resource_type,
                "resource_name": None,  # Will be enriched via API if --enrich-alicloud
                "endpoint": value_str,
                "region": region,
                "vpc_id": None,
                "shared": False,
                "services_using": [],
                "source_env_keys": set(),
                "tags": {},
                "environment": environment,
                "category": pattern_info.get("category", "other"),
                "last_discovered": None,
                "notes": None,
            }
            
            instance_ids_for_enrichment.append(instance_id)
        
        # Track env key that references this resource
        if instance_id not in source_env_keys_by_resource:
            source_env_keys_by_resource[instance_id] = set()
        source_env_keys_by_resource[instance_id].add(key)
        
        # Mark env var as classified
        classified_keys.add(key)

    # Merge source_env_keys into resources
    for instance_id, resource_dict in resources.items():
        resource_dict["source_env_keys"] = source_env_keys_by_resource.get(instance_id, set())

    return list(resources.values()), list(set(instance_ids_for_enrichment))


def build_cloud_resource_index(
    cloud_resources: list,  # List[CloudResource] Pydantic models
) -> list:  # List[CloudResourceIndex]
    """
    Build reverse-lookup index from CloudResource objects.
    
    Args:
        cloud_resources: List of CloudResource Pydantic models with populated services_using
    
    Returns:
        List of CloudResourceIndex objects
    """
    from models.service_profile import CloudResourceIndex
    
    indices = []
    for resource in cloud_resources:
        # Count unique services and shared services
        unique_services = set(resource.services_using)
        shared_services_count = len(unique_services)
        
        # Build affected_services list with namespace info
        # (In a real implementation, this would correlate with service namespaces)
        affected_services = [
            {
                "service_name": svc,
                "namespace": "unknown",  # TODO: enrich with actual namespace
            }
            for svc in unique_services
        ]
        
        # Determine criticality (can be enriched from service annotations)
        critical = shared_services_count > 3 or resource.category == "database"
        
        index = CloudResourceIndex(
            instance_id=resource.instance_id,
            resource_type=resource.resource_type,
            affected_services=affected_services,
            dependency_count=len(unique_services),
            shared_services_count=shared_services_count,
            critical=critical,
            incident_impact_estimate=None,  # TODO: estimate based on service SLAs
        )
        indices.append(index)
    
    return indices
```

---

## 3. discovery/env_parser.py — Integration

**File:** `service-discovery/discovery/env_parser.py`  
**Action:** Modify existing file

### Change 1: Add Import (after line 8)

```python
from analyzers.cloud_resource_detector import detect_cloud_resources
```

### Change 2: Modify parse_env_vars() function (lines 11-51)

Replace the entire function with:

```python
def parse_env_vars(
    workload: dict,
    environment: str,
    known_services: set[str],
) -> dict:
    """
    Takes a raw workload dict (from deployment_scanner) and returns structured dependency info.
    Now includes AliCloud resource detection.

    Args:
        workload: Raw workload dict with env_vars list
        environment: "staging" or "production"
        known_services: Set of known K8s service names (for dependency validation)

    Returns:
        Structured dict with databases, service_dependencies, external_dependencies, 
        cache_connections, cloud_resources, and alicloud_ids_for_enrichment
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

    # Collect unclassified keys (potential blind spots)
    unclassified = [
        ev["key"] for ev in env_vars
        if ev["key"] not in classified_keys
        and ev.get("source_type") != "secret"
        and ev.get("value")  # Only flag if there's an actual value worth examining
        and _looks_like_url_or_host(ev.get("value", ""))
    ]

    return {
        "databases": databases,
        "cache_connections": caches,
        "service_dependencies": service_deps,
        "external_dependencies": external_deps,
        "cloud_resources": cloud_resources,                           # NEW
        "alicloud_ids_for_enrichment": alicloud_ids_for_enrichment,   # NEW
        "raw_env_count": len(env_vars),
        "unclassified_env_keys": unclassified,
    }
```

---

## 4. main.py — Scan Pipeline

**File:** `service-discovery/main.py`  
**Action:** Modify existing scan_environment() function

### Change 1: Update Function Signature & Return Type (line 77)

Replace:
```python
def scan_environment(
    context: str,
    env_label: str,
    namespaces_filter: list[str],
    exclude: list[str],
    dry_run: bool,
    fixtures_dir: str,
    verbose: bool,
) -> list[ServiceProfile]:
```

With:
```python
def scan_environment(
    context: str,
    env_label: str,
    namespaces_filter: list[str],
    exclude: list[str],
    dry_run: bool,
    fixtures_dir: str,
    verbose: bool,
) -> tuple[list[ServiceProfile], list]:  # Returns (profiles, cloud_resources)
```

### Change 2: Initialize Cloud Resource Tracking (after line 101)

Add after `all_profiles: list[ServiceProfile] = []`:

```python
    from models.service_profile import CloudResource  # Add import at top
    
    cloud_resources_by_id: dict[str, dict] = {}  # instance_id → CloudResource dict
    all_alicloud_ids_for_enrichment: list[str] = []
```

### Change 3: Aggregate Cloud Resources (after line 142, inside the workload loop)

Add after `all_profiles.append(profile)`:

```python
                # NEW: Aggregate cloud resources across all services
                for cr_dict in parsed.get("cloud_resources", []):
                    instance_id = cr_dict.get("instance_id")
                    if instance_id:
                        if instance_id not in cloud_resources_by_id:
                            cloud_resources_by_id[instance_id] = cr_dict
                            cloud_resources_by_id[instance_id]["services_using"] = []
                        
                        # Add this service to the resource's consumers
                        svc_name = workload["name"]
                        if svc_name not in cloud_resources_by_id[instance_id]["services_using"]:
                            cloud_resources_by_id[instance_id]["services_using"].append(svc_name)
                
                # Collect IDs for optional enrichment
                all_alicloud_ids_for_enrichment.extend(
                    parsed.get("alicloud_ids_for_enrichment", [])
                )
```

### Change 4: Convert CloudResources to Pydantic Models (before return)

Replace `return all_profiles` (line 152) with:

```python    
    # Convert CloudResource dicts to Pydantic models
    from datetime import datetime
    cloud_resources = []
    for cr_dict in cloud_resources_by_id.values():
        # Mark shared resources
        cr_dict["shared"] = len(cr_dict.get("services_using", [])) > 1
        cr_dict["last_discovered"] = datetime.utcnow().isoformat()
        
        try:
            cr = CloudResource(**cr_dict)
            cloud_resources.append(cr)
        except Exception as e:
            if verbose:
                console.print(f"[yellow]⚠ Failed to create CloudResource for {cr_dict.get('instance_id')}: {e}[/yellow]")

    return all_profiles, cloud_resources
```

---

## 5. main.py — CLI Updates

**File:** `service-discovery/main.py`  
**Action:** Modify @click.command() section and main() function

### Change 1: Add CLI Option (before def main())

Add before the `@click.option("--verbose", ...)` line:

```python
@click.option("--enrich-alicloud", is_flag=True, default=False,
              help="Query AliCloud APIs to enrich resource metadata (requires API credentials)")
```

### Change 2: Update main() Function Signature

Replace:
```python
def main(context, env_label, staging_context, production_context, namespaces,
         exclude, output_dir, fmt, do_diff, dry_run, fixtures_dir, verbose):
```

With:
```python
def main(context, env_label, staging_context, production_context, namespaces,
         exclude, output_dir, fmt, do_diff, dry_run, fixtures_dir, verbose, enrich_alicloud):
```

### Change 3: Update Scan Calls (lines 240-250)

Replace:
```python
    if do_diff:
        console.print("[bold]Running diff mode — scanning both environments[/bold]")
        staging_profiles = scan_environment(stg_ctx, "staging", ns_filter, ex_list, dry_run, fdir, verbose)
        production_profiles = scan_environment(prd_ctx, "production", ns_filter, ex_list, dry_run, fdir, verbose)
        diffs = diff_environments(staging_profiles, production_profiles)
        all_profiles = staging_profiles + production_profiles
        env_label_out = "diff"
    else:
        ctx = stg_ctx if env_label == "staging" else prd_ctx
        all_profiles = scan_environment(ctx, env_label, ns_filter, ex_list, dry_run, fdir, verbose)
        env_label_out = env_label
```

With:
```python
    if do_diff:
        console.print("[bold]Running diff mode — scanning both environments[/bold]")
        staging_profiles, staging_cloud_resources = scan_environment(
            stg_ctx, "staging", ns_filter, ex_list, dry_run, fdir, verbose
        )
        production_profiles, production_cloud_resources = scan_environment(
            prd_ctx, "production", ns_filter, ex_list, dry_run, fdir, verbose
        )
        diffs = diff_environments(staging_profiles, production_profiles)
        all_profiles = staging_profiles + production_profiles
        
        # Merge cloud resources from both environments
        all_cloud_resources = _merge_cloud_resources(
            staging_cloud_resources, production_cloud_resources
        )
        env_label_out = "diff"
    else:
        ctx = stg_ctx if env_label == "staging" else prd_ctx
        all_profiles, all_cloud_resources = scan_environment(
            ctx, env_label, ns_filter, ex_list, dry_run, fdir, verbose
        )
        env_label_out = env_label
```

### Change 4: Add Cloud Resource Merge Helper (after _print_summary function, before @click.command())

```python
def _merge_cloud_resources(staging: list, production: list) -> list:
    """
    Merge cloud resources from staging and production environments.
    Combines services_using lists for shared resources.
    """
    from models.service_profile import CloudResource
    
    merged = {}
    
    for resources in [staging, production]:
        for resource in resources:
            key = resource.instance_id
            if key not in merged:
                merged[key] = resource
            else:
                # Merge services_using from both environments
                merged[key].services_using = list(
                    set(merged[key].services_using) | set(resource.services_using)
                )
                # Keep staging-level data, override with production if more recent
                if resource.last_discovered > (merged[key].last_discovered or ""):
                    merged[key] = resource
    
    return list(merged.values())
```

### Change 5: Optional AliCloud Enrichment (before export section, after diffs line)

Add after the diffs assignment block:

```python
    # Optional AliCloud API enrichment
    if enrich_alicloud and all_cloud_resources:
        if cfg.ALICLOUD_ACCESS_KEY_ID and cfg.ALICLOUD_ACCESS_KEY_SECRET:
            try:
                console.print("[bold]Enriching AliCloud resources with API metadata...[/bold]")
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
            except Exception as e:
                console.print(f"[yellow]⚠ AliCloud enrichment failed: {e}[/yellow]")
        else:
            console.print("[yellow]⚠ --enrich-alicloud set but API credentials not configured[/yellow]")
```

### Change 6: Update export_excel() Call (line 255)

Replace:
```python
        output_path = export_excel(all_profiles, diffs, env_label_out, output_dir)
```

With:
```python
        output_path = export_excel(
            all_profiles, diffs, env_label_out, output_dir,
            cloud_resources=all_cloud_resources if all_cloud_resources else None
        )
```

---

## 6. exporters/excel_exporter.py — New Sheets

**File:** `service-discovery/exporters/excel_exporter.py`  
**Action:** Add new functions and update export_excel()

### Change 1: Add Import (at top)

```python
from models.service_profile import ServiceProfile, EnvironmentDiff, CloudResource, CloudResourceIndex
```

### Change 2: Add New Sheet Function (after _sheet_env_diff function, before export_excel)

```python
# ─────────────────────────────────────────────────────────────────────────────
# Sheet 6 — Cloud Resources (NEW)
# ─────────────────────────────────────────────────────────────────────────────

def _sheet_cloud_resources(wb: Workbook, cloud_resources: list[CloudResource], env: str):
    """
    AliCloud infrastructure resources detected in the environment.
    Includes instance IDs, regions, endpoints, and services using each resource.
    """
    ws = wb.create_sheet("Cloud Resources", 6)
    headers = [
        "Instance ID", "Resource Type", "Resource Name", "Endpoint",
        "Region", "Category", "VPC ID", "Shared", "# Services Using",
        "Services Using", "Env Keys", "Environment", "Tags", "Last Discovered", "Notes",
    ]
    _apply_header(ws, headers)
    _add_timestamp_note(ws, env)

    # Color palette for resource types
    type_colors = {
        "rds_postgresql": "DBEAFE",
        "rds_mysql": "EDE9FE",
        "rds_mongodb": "D1FAE5",
        "redis": "FEF3C7",
        "elasticsearch": "FEE2E2",
        "oss_bucket": "F3E8FF",
        "mns_queue": "ECFDF5",
        "kafka_instance": "CFFAFE",
        "api_gateway": "FECACA",
    }

    row = 2
    for resource in sorted(cloud_resources, key=lambda r: r.instance_id):
        services_str = "; ".join(resource.services_using) if resource.services_using else "—"
        env_keys_str = "; ".join(resource.source_env_keys) if resource.source_env_keys else "—"
        tags_str = "; ".join([f"{k}={v}" for k, v in resource.tags.items()]) if resource.tags else ""
        
        values = [
            resource.instance_id,
            resource.resource_type,
            resource.resource_name or "",
            resource.endpoint or "",
            resource.region or "",
            resource.category,
            resource.vpc_id or "",
            "Yes" if resource.shared else "No",
            len(resource.services_using),
            services_str,
            env_keys_str,
            resource.environment or "",
            tags_str,
            resource.last_discovered or "",
            resource.notes or "",
        ]
        
        for col, v in enumerate(values, 1):
            ws.cell(row=row, column=col, value=v)
        
        # Color by resource type
        color = type_colors.get(resource.resource_type, "FFFFFF")
        _style_data_row(ws, row, len(headers), color)
        
        # Highlight if shared across multiple services
        if resource.shared and len(resource.services_using) > 1:
            ws.cell(row=row, column=8).fill = _fill(ORANGE_FILL)
        
        row += 1

    if row == 2:
        ws.cell(row=2, column=1, value="No cloud resources detected").font = _font(bold=True)

    _freeze_and_filter(ws)
    _auto_width(ws, min_w=12, max_w=70)


# ─────────────────────────────────────────────────────────────────────────────
# Sheet 7 — Reverse Dependency Index (NEW)
# ─────────────────────────────────────────────────────────────────────────────

def _sheet_reverse_index(wb: Workbook, cloud_resources_index: list[CloudResourceIndex]):
    """
    Reverse-lookup materialized view for incident response.
    Key question: 'This RDS instance is down — which services are affected?'
    """
    ws = wb.create_sheet("Reverse Dependency Index", 7)
    headers = [
        "Instance ID", "Resource Type", "# Services Affected", "Affected Services",
        "Shared (same resource)", "Critical", "Incident Impact Estimate",
    ]
    _apply_header(ws, headers)

    row = 2
    for index in sorted(cloud_resources_index, key=lambda x: x.dependency_count, reverse=True):
        affected_str = "; ".join([
            f"{s['service_name']}" + (f" ({s['namespace']})" if s.get('namespace') != 'unknown' else "")
            for s in index.affected_services
        ])
        
        values = [
            index.instance_id,
            index.resource_type,
            index.dependency_count,
            affected_str,
            index.shared_services_count,
            "Yes" if index.critical else "No",
            index.incident_impact_estimate or "",
        ]
        
        for col, v in enumerate(values, 1):
            ws.cell(row=row, column=col, value=v)
        
        # Color by severity: critical high-impact = red, shared = orange, normal = green
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

### Change 3: Update export_excel() Function Signature

Replace the function signature (around line ~300):

```python
def export_excel(
    profiles: list[ServiceProfile],
    diffs: list[EnvironmentDiff],
    env_label: str,
    output_dir: str,
    cloud_resources: Optional[list[CloudResource]] = None,
) -> str:
    """
    Exports to an Excel workbook with service discovery data.
    
    Args:
        profiles: List of ServiceProfile objects
        diffs: List of EnvironmentDiff objects (from diff mode)
        env_label: Environment label for naming ("staging", "production", "diff")
        output_dir: Output directory path
        cloud_resources: Optional list of CloudResource objects (AliCloud resources)
    
    Returns:
        Path to the generated .xlsx file
    """
```

### Change 4: Update export_excel() Sheet Creation (inside function body)

Find the sheet creation section and replace with:

```python    
    wb = Workbook()
    wb.remove(wb.active)  # Remove default sheet

    _sheet_services_overview(wb, profiles, env_label)
    _sheet_databases(wb, profiles, env_label)
    # _sheet_caches(wb, profiles, env_label)  # If this function exists
    _sheet_service_deps(wb, profiles, env_label)
    _sheet_external_deps(wb, profiles, env_label)
    
    if diffs:
        _sheet_env_diff(wb, diffs)
    
    # NEW: Cloud resources and reverse index
    if cloud_resources:
        from analyzers.cloud_resource_detector import build_cloud_resource_index
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

## 7. exporters/graph_exporter.py — NEW FILE

**Action:** Create new file at `service-discovery/exporters/graph_exporter.py`

```python
"""
Exports service and cloud resource dependencies as an interactive HTML graph.
Uses D3.js force-directed layout for visualization.
"""

import json
import os
from datetime import datetime
from typing import Optional
from pathlib import Path

from models.service_profile import ServiceProfile, CloudResource


def export_html_graph(
    profiles: list[ServiceProfile],
    cloud_resources: Optional[list[CloudResource]],
    env_label: str,
    output_dir: str,
) -> str:
    """
    Generates an interactive HTML dependency graph.
    
    Args:
        profiles: List of ServiceProfile objects
        cloud_resources: Optional list of CloudResource objects
        env_label: Environment label for the title
        output_dir: Output directory path
    
    Returns:
        Path to the generated .html file
    """
    if cloud_resources is None:
        cloud_resources = []

    # Build graph data structure
    nodes = []
    edges = []
    node_ids = set()

    # Add service nodes
    for p in profiles:
        node_id = f"svc:{p.name}:{p.namespace}"
        if node_id not in node_ids:
            nodes.append({
                "id": node_id,
                "label": p.name,
                "type": "service",
                "namespace": p.namespace,
                "environment": p.environment,
                "group": "services",
                "size": 15,
                "color": "#4299E1",
            })
            node_ids.add(node_id)

    # Add cloud resource nodes
    for cr in cloud_resources:
        node_id = f"res:{cr.instance_id}"
        if node_id not in node_ids:
            # Color by resource type
            color_map = {
                "rds_postgresql": "#3B82F6",
                "rds_mysql": "#8B5CF6",
                "rds_mongodb": "#10B981",
                "redis": "#FBBF24",
                "elasticsearch": "#F87171",
                "oss_bucket": "#A78BFA",
                "kafka_instance": "#06B6D4",
            }
            color = color_map.get(cr.resource_type, "#6B7280")
            
            nodes.append({
                "id": node_id,
                "label": cr.resource_name or cr.instance_id,
                "type": cr.resource_type,
                "category": cr.category,
                "group": "resources",
                "size": max(10, min(25, len(cr.services_using))),  # Size by usage
                "color": color,
            })
            node_ids.add(node_id)

    # Add service → service edges (inter-service dependencies)
    for p in profiles:
        for dep in p.service_dependencies:
            edge_target = f"svc:{dep.target_service}:{dep.target_namespace}"
            edges.append({
                "source": f"svc:{p.name}:{p.namespace}",
                "target": edge_target,
                "type": "service_dep",
                "protocol": dep.protocol,
                "validated": dep.validated,
                "width": 2 if dep.validated else 1,
                "color": "#059669" if dep.validated else "#DC2626",
            })

    # Add service → cloud resource edges
    for p in profiles:
        for db in p.databases:
            # Try to find matching cloud resource
            for cr in cloud_resources:
                if cr.instance_id == db.instance_id or db.host in (cr.endpoint or ""):
                    edges.append({
                        "source": f"svc:{p.name}:{p.namespace}",
                        "target": f"res:{cr.instance_id}",
                        "type": "database",
                        "db_type": db.db_type,
                        "width": 2,
                        "color": "#3B82F6",
                    })
                    break
        
        for cache in p.cache_connections:
            # Try to find matching cloud resource
            for cr in cloud_resources:
                if cr.instance_id == cache.instance_id or cache.host in (cr.endpoint or ""):
                    edges.append({
                        "source": f"svc:{p.name}:{p.namespace}",
                        "target": f"res:{cr.instance_id}",
                        "type": "cache",
                        "cache_type": cache.cache_type,
                        "width": 2,
                        "color": "#FBBF24",
                    })
                    break

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
    
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Service Discovery Dependency Graph - {env_label}</title>
    <script src="https://d3js.org/d3.v7.min.js"></script>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #f3f4f6; }}
        
        #container {{ display: flex; height: 100vh; }}
        
        #graph-container {{
            flex: 1;
            background: white;
            position: relative;
            border-right: 1px solid #e5e7eb;
        }}
        
        svg {{ display: block; width: 100%; height: 100%; }}
        
        .node {{
            cursor: pointer;
            stroke: white;
            stroke-width: 2px;
        }}
        
        .node:hover {{
            stroke: #333;
            stroke-width: 3px;
        }}
        
        .link {{
            stroke-opacity: 0.6;
            stroke-linecap: round;
        }}
        
        .node-label {{
            font-size: 11px;
            font-weight: 500;
            text-anchor: middle;
            pointer-events: none;
            user-select: none;
            fill: #1f2937;
        }}
        
        #info-panel {{
            width: 300px;
            padding: 20px;
            overflow-y: auto;
            background: #f9fafb;
            border-left: 1px solid #e5e7eb;
        }}
        
        .info-section {{ margin-bottom: 20px; }}
        .info-label {{ font-size: 12px; font-weight: 600; color: #6b7280; text-transform: uppercase; margin-bottom: 8px; }}
        .info-value {{ font-size: 14px; color: #1f2937; word-break: break-word; }}
        
        .legend {{
            position: absolute;
            bottom: 20px;
            left: 20px;
            background: white;
            padding: 15px;
            border-radius: 8px;
            border: 1px solid #e5e7eb;
            font-size: 12px;
        }}
        
        .legend-item {{ margin-bottom: 8px; display: flex; align-items: center; }}
        .legend-color {{ width: 16px; height: 16px; border-radius: 50%; margin-right: 8px; }}
        
        h1 {{ font-size: 18px; font-weight: 600; margin-bottom: 20px; color: #1f2937; }}
        h2 {{ font-size: 14px; font-weight: 600; color: #374151; margin-bottom: 12px; margin-top: 16px; }}
        
        .stats {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 20px; }}
        .stat {{ background: white; padding: 12px; border-radius: 6px; border: 1px solid #e5e7eb; }}
        .stat-label {{ font-size: 11px; color: #6b7280; margin-bottom: 4px; }}
        .stat-value {{ font-size: 18px; font-weight: 600; color: #1f2937; }}
    </style>
</head>
<body>
    <div id="container">
        <div id="graph-container">
            <svg id="graph"></svg>
            <div class="legend">
                <div class="legend-item">
                    <div class="legend-color" style="background: #4299E1;"></div>
                    <span>Service</span>
                </div>
                <div class="legend-item">
                    <div class="legend-color" style="background: #3B82F6;"></div>
                    <span>Database</span>
                </div>
                <div class="legend-item">
                    <div class="legend-color" style="background: #FBBF24;"></div>
                    <span>Cache</span>
                </div>
                <div class="legend-item">
                    <div class="legend-color" style="background: #6B7280;"></div>
                    <span>Other Resource</span>
                </div>
            </div>
        </div>
        <div id="info-panel">
            <h1>Service Dependency Graph</h1>
            <p style="color: #6b7280; font-size: 13px; margin-bottom: 20px;">Environment: <strong>{env_label}</strong></p>
            
            <div class="stats">
                <div class="stat">
                    <div class="stat-label">Services</div>
                    <div class="stat-value" id="stat-services">0</div>
                </div>
                <div class="stat">
                    <div class="stat-label">Resources</div>
                    <div class="stat-value" id="stat-resources">0</div>
                </div>
            </div>
            
            <div id="node-info" style="display: none;">
                <h2 id="info-title">—</h2>
                <div class="info-section">
                    <div class="info-label">Type</div>
                    <div class="info-value" id="info-type">—</div>
                </div>
                <div class="info-section">
                    <div class="info-label">Category</div>
                    <div class="info-value" id="info-category">—</div>
                </div>
                <div class="info-section">
                    <div class="info-label">Connected Services</div>
                    <div class="info-value" id="info-connections">—</div>
                </div>
                <div class="info-section">
                    <div class="info-label">Details</div>
                    <div class="info-value" id="info-details">—</div>
                </div>
            </div>
        </div>
    </div>

    <script>
        const nodes = {nodes_json};
        const links = {edges_json};
        
        // Initialize D3 visualization
        const svg = d3.select("#graph");
        const width = svg.node().clientWidth;
        const height = svg.node().clientHeight;
        
        const simulation = d3.forceSimulation(nodes)
            .force("link", d3.forceLink(links)
                .id(d => d.id)
                .distance(80)
                .strength(0.3))
            .force("charge", d3.forceManyBody().strength(-300))
            .force("center", d3.forceCenter(width / 2, height / 2))
            .force("collision", d3.forceCollide().radius(30));
        
        // Render links
        const link = svg.selectAll(".link")
            .data(links)
            .enter().append("line")
            .attr("class", "link")
            .attr("stroke", d => d.color)
            .attr("stroke-width", d => d.width || 1);
        
        // Render nodes
        const node = svg.selectAll(".node")
            .data(nodes)
            .enter().append("circle")
            .attr("class", "node")
            .attr("r", d => d.size || 10)
            .attr("fill", d => d.color)
            .attr("data-id", d => d.id)
            .call(d3.drag()
                .on("start", dragStarted)
                .on("drag", dragged)
                .on("end", dragEnded));
        
        node.on("click", showNodeInfo);
        
        // Render labels
        const labels = svg.selectAll(".node-label")
            .data(nodes)
            .enter().append("text")
            .attr("class", "node-label")
            .text(d => d.label)
            .attr("dy", "0.3em");
        
        // Update positions on simulation tick
        simulation.on("tick", () => {{
            link
                .attr("x1", d => d.source.x)
                .attr("y1", d => d.source.y)
                .attr("x2", d => d.target.x)
                .attr("y2", d => d.target.y);
            
            node
                .attr("cx", d => d.x)
                .attr("cy", d => d.y);
            
            labels
                .attr("x", d => d.x)
                .attr("y", d => d.y);
        }});
        
        // Drag functions
        function dragStarted(event, d) {{
            if (!event.active) simulation.alphaTarget(0.3).restart();
            d.fx = d.x;
            d.fy = d.y;
        }}
        
        function dragged(event, d) {{
            d.fx = event.x;
            d.fy = event.y;
        }}
        
        function dragEnded(event, d) {{
            if (!event.active) simulation.alphaTarget(0);
            d.fx = null;
            d.fy = null;
        }}
        
        // Show node info panel
        function showNodeInfo(event, d) {{
            const infoPanel = document.getElementById("node-info");
            document.getElementById("info-title").textContent = d.label;
            document.getElementById("info-type").textContent = d.type || "—";
            document.getElementById("info-category").textContent = d.category || d.group;
            
            const connections = links.filter(l => l.source.id === d.id || l.target.id === d.id).length;
            document.getElementById("info-connections").textContent = connections || "None";
            
            let details = "";
            if (d.namespace) details += "Namespace: " + d.namespace + "\\n";
            if (d.environment) details += "Environment: " + d.environment + "\\n";
            document.getElementById("info-details").textContent = details || "—";
            
            infoPanel.style.display = "block";
        }}
        
        // Update stats
        document.getElementById("stat-services").textContent = nodes.filter(n => n.group === "services").length;
        document.getElementById("stat-resources").textContent = nodes.filter(n => n.group === "resources").length;
    </script>
</body>
</html>"""
    
    return html
```

---

## 8. discovery/alicloud_enricher.py — Optional Enrichment (OUTLINE)

**Action:** Create new file at `service-discovery/discovery/alicloud_enricher.py`

This is an outline showing the structure. Full implementation requires AliCloud SDK:

```python
"""
Optional AliCloud API enrichment module.
Queries AliCloud APIs to enrich discovered resources with metadata.
Only imported if --enrich-alicloud flag is set and credentials are configured.
"""

import asyncio
from typing import Optional
from models.service_profile import CloudResource


class AliCloudEnricher:
    """Enriches CloudResource objects with AliCloud metadata."""
    
    def __init__(
        self,
        access_key_id: str,
        access_key_secret: str,
        regions: list[str],
        timeout_sec: int = 10,
    ):
        self.access_key_id = access_key_id
        self.access_key_secret = access_key_secret
        self.regions = regions
        self.timeout_sec = timeout_sec
        self._clients = {}  # Lazy-loaded clients per region
    
    async def enrich_resource(self, resource: CloudResource) -> CloudResource:
        """Enriches a single CloudResource with metadata from AliCloud APIs."""
        resource_type = resource.resource_type
        
        try:
            if resource_type.startswith("rds_"):
                await self._enrich_rds(resource)
            elif resource_type == "redis":
                await self._enrich_redis(resource)
            elif resource_type == "elasticsearch":
                await self._enrich_elasticsearch(resource)
            elif resource_type == "oss_bucket":
                await self._enrich_oss(resource)
            # Add other resource types as needed
        except Exception as e:
            # Graceful degradation: return resource unchanged
            print(f"Warning: Failed to enrich {resource.instance_id}: {e}")
        
        return resource
    
    async def _enrich_rds(self, resource: CloudResource):
        """Query RDS API for database specs, VPC, backups, etc."""
        # Implementation would use: from alibabacloud_rds20160815.client import Client as RdsClient
        # Fetch: cpu, memory, storage, replica count, backup status
        # Update resource.tags and resource.notes with metrics
        pass
    
    async def _enrich_redis(self, resource: CloudResource):
        """Query Redis API for instance specs."""
        pass
    
    async def _enrich_elasticsearch(self, resource: CloudResource):
        """Query Elasticsearch API for node count, node type, storage."""
        pass
    
    async def _enrich_oss(self, resource: CloudResource):
        """Query OSS API for bucket size, object count."""
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
    semaphore = asyncio.Semaphore(5)  # Limit to 5 concurrent requests
    
    async def enrich_with_semaphore(resource):
        async with semaphore:
            return await enricher.enrich_resource(resource)
    
    tasks = [enrich_with_semaphore(r) for r in resources]
    enriched = await asyncio.gather(*tasks)
    
    return enriched
```

---

## Summary of Changes

| File | Changes | Lines |
|------|---------|-------|
| `config.py` | Add AliCloud settings | +15 |
| `discovery/env_parser.py` | Import + call detect_cloud_resources() | +2 import, +5 call |
| `analyzers/cloud_resource_detector.py` | NEW full module | ~350 |
| `main.py` | Aggregate resources, add CLI flag, optional enrichment, merge helper | +90 |
| `exporters/excel_exporter.py` | 2 new sheets + update signature | +120 |
| `exporters/graph_exporter.py` | NEW full module | ~300 |
| `discovery/alicloud_enricher.py` | NEW optional enrichment module | ~150 |

**Total: ~900 lines of new/modified code**

---

## Integration Order

1. **First:** Create `analyzers/cloud_resource_detector.py`
2. **Second:** Modify `discovery/env_parser.py` to call it
3. **Third:** Modify `main.py` scan pipeline to aggregate resources
4. **Fourth:** Update `config.py` with new settings
5. **Fifth:** Add new sheets to `exporters/excel_exporter.py`
6. **Sixth:** Update `main.py` CLI and export calls
7. **Seventh:** Create `exporters/graph_exporter.py` (optional)
8. **Eighth:** Create `discovery/alicloud_enricher.py` (optional)
9. **Finally:** Test with fixtures and live data

