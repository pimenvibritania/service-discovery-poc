"""
Detect and enrich Alibaba Cloud resources from environment variables, databases, caches, and external deps.

This module provides comprehensive detection of AliCloud resource patterns including:
- RDS (PostgreSQL, MySQL), ApsaraDB (MongoDB, Redis)
- Elasticsearch, OSS, SLS, Kafka, RocketMQ, MNS
- SLB/ALB, PrivateZone, Direct Mail, SMS, API Gateway
- Function Compute, MaxCompute, NAS, ACR, Table Store, AnalyticDB

Uses pattern registry approach — add new resource types here as they're discovered.
"""

import re
from typing import Optional


# ─────────────────────────────────────────────────────────────────────────────
# AliCloud Resource Pattern Registry
# ─────────────────────────────────────────────────────────────────────────────

ALICLOUD_RESOURCE_PATTERNS = {
    "rds_postgresql": {
        "resource_type": "RDS PostgreSQL",
        "category": "database",
        "hostname_patterns": [
            r"pgm-[\w-]+\.pg\.rds\.aliyuncs\.com",
            r"rm-[\w-]+\.pg\.rds\.aliyuncs\.com",
        ],
        "instance_prefixes": ["pgm-", "rm-"],
        "env_keys": [
            r"(?i)postgres.*endpoint",
            r"(?i)pgsql.*endpoint",
            r"(?i)pg.*rds.*endpoint",
        ],
    },
    "rds_mysql": {
        "resource_type": "RDS MySQL",
        "category": "database",
        "hostname_patterns": [
            r"rm-[\w-]+\.mysql\.rds\.aliyuncs\.com",
        ],
        "instance_prefixes": ["rm-"],
        "env_keys": [
            r"(?i)mysql.*rds.*endpoint",
            r"(?i)mysql.*endpoint",
        ],
    },
    "apsaradb_mongodb": {
        "resource_type": "ApsaraDB MongoDB",
        "category": "database",
        "hostname_patterns": [
            r"dds-[\w-]+\.mongodb\.rds\.aliyuncs\.com",
        ],
        "instance_prefixes": ["dds-"],
        "env_keys": [
            r"(?i)mongo.*rds.*endpoint",
            r"(?i)mongodb.*rds.*endpoint",
        ],
    },
    "apsaradb_redis": {
        "resource_type": "ApsaraDB Redis",
        "category": "cache",
        "hostname_patterns": [
            r"r-[\w-]+\.redis\.rds\.aliyuncs\.com",
        ],
        "instance_prefixes": ["r-"],
        "env_keys": [
            r"(?i)redis.*rds.*endpoint",
            r"(?i)cache.*redis.*endpoint",
        ],
    },
    "elasticsearch": {
        "resource_type": "Elasticsearch",
        "category": "search",
        "hostname_patterns": [
            r"es-[\w-]+\.elasticsearch\.aliyuncs\.com",
        ],
        "instance_prefixes": ["es-"],
        "env_keys": [
            r"(?i)elasticsearch.*endpoint",
            r"(?i)elastic.*endpoint",
            r"(?i)es.*endpoint",
        ],
    },
    "oss": {
        "resource_type": "Object Storage Service (OSS)",
        "category": "storage",
        "hostname_patterns": [
            r"[\w-]+\.oss-[\w-]+\.aliyuncs\.com",
            r"[\w-]+\.oss-[\w-]+-internal\.aliyuncs\.com",
        ],
        "instance_prefixes": None,  # Extracted from bucket name
        "env_keys": [
            r"(?i)oss.*endpoint",
            r"(?i)oss.*bucket",
            r"(?i)bucket.*endpoint",
            r"(?i)s3.*endpoint",  # S3-compatible naming
        ],
    },
    "sls": {
        "resource_type": "Simple Log Service (SLS)",
        "category": "logging",
        "hostname_patterns": [
            r"[\w-]+\.[\w-]+\.log\.aliyuncs\.com",
        ],
        "instance_prefixes": None,  # Extracted from project name
        "env_keys": [
            r"(?i)sls.*endpoint",
            r"(?i)log.*endpoint",
            r"(?i)logging.*endpoint",
        ],
    },
    "kafka": {
        "resource_type": "Message Queue for Apache Kafka",
        "category": "message_queue",
        "hostname_patterns": [
            r"alikafka-post-[\w-]+\.alikafka\.aliyuncs\.com",
            r"[\w-]+\.kafka\.aliyuncs\.com",
        ],
        "instance_prefixes": ["alikafka-post-"],
        "env_keys": [
            r"(?i)kafka.*broker",
            r"(?i)kafka.*endpoint",
            r"(?i)kafka.*bootstrap",
            r"(?i)kafka.*host",
        ],
    },
    "rocketmq": {
        "resource_type": "Message Queue for Apache RocketMQ",
        "category": "message_queue",
        "hostname_patterns": [
            r"[\w-]+\.mqrest\.[\w-]+\.aliyuncs\.com",
        ],
        "instance_prefixes": None,
        "env_keys": [
            r"(?i)rocketmq.*endpoint",
            r"(?i)mq.*endpoint",
            r"(?i)ons.*endpoint",
            r"(?i)ons.*addr",
        ],
    },
    "mns": {
        "resource_type": "Message Notification Service (MNS)",
        "category": "message_queue",
        "hostname_patterns": [
            r"[\w-]+\.mns\.[\w-]+\.aliyuncs\.com",
        ],
        "instance_prefixes": None,
        "env_keys": [
            r"(?i)mns.*endpoint",
            r"(?i)mns.*queue.*endpoint",
        ],
    },
    "slb": {
        "resource_type": "Server Load Balancer (SLB)",
        "category": "networking",
        "hostname_patterns": [
            r"slb-[\w-]+",
        ],
        "instance_prefixes": ["slb-"],
        "env_keys": [
            r"(?i)slb.*endpoint",
            r"(?i)load.*balancer.*endpoint",
        ],
    },
    "alb": {
        "resource_type": "Application Load Balancer (ALB)",
        "category": "networking",
        "hostname_patterns": [
            r"alb-[\w-]+",
        ],
        "instance_prefixes": ["alb-"],
        "env_keys": [
            r"(?i)alb.*endpoint",
            r"(?i)application.*load.*balancer",
        ],
    },
    "direct_mail": {
        "resource_type": "Direct Mail",
        "category": "email",
        "hostname_patterns": [
            r"dm\.aliyuncs\.com",
        ],
        "instance_prefixes": None,
        "env_keys": [
            r"(?i)direct.*mail.*endpoint",
            r"(?i)dm.*endpoint",
            r"(?i)email.*service.*endpoint",
        ],
    },
    "sms": {
        "resource_type": "Short Message Service (SMS)",
        "category": "communication",
        "hostname_patterns": [
            r"dysmsapi\.aliyuncs\.com",
        ],
        "instance_prefixes": None,
        "env_keys": [
            r"(?i)sms.*endpoint",
            r"(?i)dysmsapi.*endpoint",
        ],
    },
    "api_gateway": {
        "resource_type": "API Gateway",
        "category": "api",
        "hostname_patterns": [
            r"[\w-]+\.[\w-]+\.api\.aliyuncs\.com",
        ],
        "instance_prefixes": None,
        "env_keys": [
            r"(?i)api.*gateway.*endpoint",
            r"(?i)apigateway.*endpoint",
        ],
    },
    "function_compute": {
        "resource_type": "Function Compute",
        "category": "compute",
        "hostname_patterns": [
            r"[\w-]+\.fc\.aliyuncs\.com",
            r"[\w-]+\.[\w-]+\.fc\.aliyuncs\.com",
        ],
        "instance_prefixes": None,
        "env_keys": [
            r"(?i)function.*compute.*endpoint",
            r"(?i)fc.*endpoint",
        ],
    },
    "maxcompute": {
        "resource_type": "MaxCompute (ODPS)",
        "category": "analytics",
        "hostname_patterns": [
            r"[\w-]+\.odps\.aliyuncs\.com",
        ],
        "instance_prefixes": None,
        "env_keys": [
            r"(?i)maxcompute.*endpoint",
            r"(?i)odps.*endpoint",
        ],
    },
    "nas": {
        "resource_type": "Network Attached Storage (NAS)",
        "category": "storage",
        "hostname_patterns": [
            r"[\w-]+\.nas\.aliyuncs\.com",
        ],
        "instance_prefixes": None,
        "env_keys": [
            r"(?i)nas.*endpoint",
            r"(?i)nas.*mount",
        ],
    },
    "acr": {
        "resource_type": "Alibaba Cloud Container Registry (ACR)",
        "category": "registry",
        "hostname_patterns": [
            r"registry\.[\w-]+\.aliyuncs\.com",
            r"[\w-]+-registry\.[\w-]+\.cr\.aliyuncs\.com",
        ],
        "instance_prefixes": None,
        "env_keys": [
            r"(?i)registry.*endpoint",
            r"(?i)acr.*endpoint",
            r"(?i)container.*registry.*endpoint",
        ],
    },
    "ots": {
        "resource_type": "Table Store (OTS)",
        "category": "database",
        "hostname_patterns": [
            r"[\w-]+\.[\w-]+\.ots\.aliyuncs\.com",
        ],
        "instance_prefixes": None,
        "env_keys": [
            r"(?i)table.*store.*endpoint",
            r"(?i)ots.*endpoint",
        ],
    },
    "analyticdb": {
        "resource_type": "AnalyticDB",
        "category": "analytics",
        "hostname_patterns": [
            r"am-[\w-]+\.ads\.aliyuncs\.com",
        ],
        "instance_prefixes": ["am-"],
        "env_keys": [
            r"(?i)analyticdb.*endpoint",
            r"(?i)ads.*endpoint",
        ],
    },
    "privatezone": {
        "resource_type": "PrivateZone DNS",
        "category": "networking",
        "hostname_patterns": [
            r"[\w-]+\.private\.aliyuncs\.com",
        ],
        "instance_prefixes": None,
        "env_keys": [
            r"(?i)privatezone.*endpoint",
            r"(?i)private.*dns.*endpoint",
        ],
    },
}


# Environment variable key patterns that typically point to AliCloud resources
ALICLOUD_ENV_KEY_PATTERNS = {
    "oss": [
        r"(?i)oss.*endpoint",
        r"(?i)oss.*bucket",
        r"(?i)bucket.*name",
        r"(?i)bucket.*endpoint",
        r"(?i)s3.*endpoint",
    ],
    "sls": [
        r"(?i)sls.*endpoint",
        r"(?i)log.*endpoint",
        r"(?i)log.*project",
        r"(?i)logging.*endpoint",
    ],
    "kafka": [
        r"(?i)kafka.*broker",
        r"(?i)kafka.*bootstrap",
        r"(?i)kafka.*host",
        r"(?i)kafka.*endpoint",
    ],
    "rocketmq": [
        r"(?i)rocketmq.*endpoint",
        r"(?i)mq.*endpoint",
        r"(?i)ons.*addr",
        r"(?i)ons.*endpoint",
    ],
    "mns": [
        r"(?i)mns.*endpoint",
        r"(?i)mns.*queue",
    ],
    "elasticsearch": [
        r"(?i)elasticsearch.*endpoint",
        r"(?i)elastic.*endpoint",
        r"(?i)es.*endpoint",
    ],
}


# ─────────────────────────────────────────────────────────────────────────────
# Helper Functions
# ─────────────────────────────────────────────────────────────────────────────

def _matches_any(value: str, patterns: list[str]) -> bool:
    """Check if value matches any regex pattern."""
    return any(re.search(p, value, re.IGNORECASE) for p in patterns)


def _extract_instance_id_from_hostname(hostname: str, prefixes: Optional[list[str]]) -> Optional[str]:
    """
    Extract AliCloud instance ID from hostname by matching known prefixes.

    Examples:
        - "pgm-abc123def.pg.rds.aliyuncs.com" -> "pgm-abc123def"
        - "rm-xyz789.mysql.rds.aliyuncs.com" -> "rm-xyz789"
        - "dds-qwerty.mongodb.rds.aliyuncs.com" -> "dds-qwerty"
    """
    if not hostname or not prefixes:
        return None

    # Extract first part (before first dot)
    first_part = hostname.split(".")[0]

    # Check if it matches any known prefix
    for prefix in prefixes:
        if first_part.startswith(prefix):
            return first_part

    return None


def _extract_bucket_and_region_from_oss(hostname: str) -> tuple[Optional[str], Optional[str]]:
    """
    Extract bucket name and region from OSS hostname.

    Examples:
        - "mybucket.oss-ap-southeast-1.aliyuncs.com" -> ("mybucket", "ap-southeast-1")
        - "data.oss-cn-hangzhou-internal.aliyuncs.com" -> ("data", "cn-hangzhou")
    """
    if not hostname:
        return None, None

    parts = hostname.split(".")
    if not parts:
        return None, None

    bucket = parts[0]

    # Extract region from oss-{region} or oss-{region}-internal pattern
    region = None
    for part in parts:
        if part.startswith("oss-"):
            # Remove "oss-" prefix and "-internal" suffix if present
            region = part[4:]  # Skip "oss-"
            if region.endswith("-internal"):
                region = region[:-9]  # Remove "-internal"
            break

    return bucket, region


def _extract_project_and_region_from_sls(hostname: str) -> tuple[Optional[str], Optional[str]]:
    """
    Extract project name and region from SLS hostname.

    Examples:
        - "myproject.ap-southeast-1.log.aliyuncs.com" -> ("myproject", "ap-southeast-1")
        - "logs.cn-hangzhou.log.aliyuncs.com" -> ("logs", "cn-hangzhou")
    """
    if not hostname:
        return None, None

    parts = hostname.split(".")
    if len(parts) < 3:
        return None, None

    # Format: {project}.{region}.log.aliyuncs.com
    project = parts[0]
    region = None

    # Find "log" marker to identify region
    try:
        log_idx = parts.index("log")
        if log_idx > 0:
            region = parts[log_idx - 1]
    except (ValueError, IndexError):
        pass

    return project, region


def _extract_region_from_hostname(hostname: str) -> Optional[str]:
    """
    Extract AliCloud region from a hostname.

    Looks for patterns like:
        - oss-{region}.aliyuncs.com
        - {region}.log.aliyuncs.com
        - {region}.ots.aliyuncs.com
        - etc.

    Returns standard AliCloud region codes like: cn-hangzhou, ap-southeast-1, etc.
    """
    if not hostname:
        return None

    # Pattern for standard AliCloud regions
    region_pattern = r"(cn-[\w-]+|ap-[\w-]+|us-[\w-]+|eu-[\w-]+|me-[\w-]+|sa-[\w-]+)"
    match = re.search(region_pattern, hostname, re.IGNORECASE)
    if match:
        return match.group(1)

    return None


def _match_resource_type(hostname: str, value: str) -> Optional[str]:
    """
    Identify AliCloud resource type by matching hostname/value against patterns.

    Returns: resource_type key from ALICLOUD_RESOURCE_PATTERNS, or None.
    """
    test_value = f"{hostname}:{value}".lower()

    for resource_type, config in ALICLOUD_RESOURCE_PATTERNS.items():
        # Try hostname patterns
        for pattern in config.get("hostname_patterns", []):
            if re.search(pattern, test_value, re.IGNORECASE):
                return resource_type

    return None


def _is_alicloud_resource(value: str) -> bool:
    """Quick check if a value looks like an AliCloud resource."""
    return bool(re.search(r"\.aliyuncs\.com", value, re.IGNORECASE))


# ─────────────────────────────────────────────────────────────────────────────
# Main Detection Function
# ─────────────────────────────────────────────────────────────────────────────

def detect_cloud_resources(
    env_vars: list[dict],
    databases: list[dict],
    caches: list[dict],
    external_deps: list[dict],
    environment: str,
) -> list[dict]:
    """
    Detect and enrich AliCloud resources from environment variables, databases, caches, and external deps.

    Enriches already-detected databases, caches, and external deps with AliCloud-specific metadata
    (resource_type, region, instance_id, etc.), and detects additional AliCloud resources not
    caught by other detectors.

    Args:
        env_vars: List of environment variable dicts {key, value, source_type, source_name, container_name}
        databases: List of detected DatabaseConnection dicts from db_detector.py
        caches: List of detected CacheConnection dicts from db_detector.py
        external_deps: List of detected ExternalDependency dicts from external_dep_detector.py
        environment: Deployment environment (staging, production, etc.)

    Returns:
        List of CloudResource dicts with fields:
            - resource_type: Human-readable type (e.g., "RDS PostgreSQL", "Object Storage Service (OSS)")
            - category: Resource category (database, cache, storage, logging, etc.)
            - instance_id: AliCloud instance/resource ID (e.g., "pgm-abc123def", "mybucket")
            - resource_name: Friendly name for the resource
            - region: AliCloud region (e.g., "ap-southeast-1", "cn-hangzhou")
            - endpoint: Full hostname/endpoint of the resource
            - source_env_keys: List of environment variable names pointing to this resource
            - source_type: How discovered (configmap, secret, direct, database, cache, external_dep)
            - source_container: Container name using this resource (if known)
            - environment: Deployment environment
    """
    found = []
    seen: set[str] = set()  # Deduplication: (resource_type, instance_id, region, endpoint)

    # ─────────────────────────────────────────────────────────────────────────
    # Phase 1: Enrich existing databases with AliCloud metadata
    # ─────────────────────────────────────────────────────────────────────────
    for db in databases:
        host = db.get("host", "")
        if not _is_alicloud_resource(host):
            continue

        instance_id = db.get("instance_id")
        resource_type = _match_resource_type(host, "")
        region = _extract_region_from_hostname(host)

        if resource_type:
            dedup_key = (resource_type, instance_id, region, host)
            if dedup_key not in seen:
                seen.add(dedup_key)
                found.append({
                    "resource_type": ALICLOUD_RESOURCE_PATTERNS[resource_type]["resource_type"],
                    "category": ALICLOUD_RESOURCE_PATTERNS[resource_type]["category"],
                    "instance_id": instance_id,
                    "resource_name": f"{ALICLOUD_RESOURCE_PATTERNS[resource_type]['resource_type']} ({instance_id or host})",
                    "region": region,
                    "endpoint": host,
                    "source_env_keys": [db.get("source_env_key", "")],
                    "source_type": "database",
                    "source_container": db.get("container_name", "unknown"),
                    "environment": environment,
                })

    # ─────────────────────────────────────────────────────────────────────────
    # Phase 2: Enrich existing caches with AliCloud metadata
    # ─────────────────────────────────────────────────────────────────────────
    for cache in caches:
        host = cache.get("host", "")
        if not _is_alicloud_resource(host):
            continue

        instance_id = cache.get("instance_id")
        resource_type = _match_resource_type(host, "")
        region = _extract_region_from_hostname(host)

        if resource_type:
            dedup_key = (resource_type, instance_id, region, host)
            if dedup_key not in seen:
                seen.add(dedup_key)
                found.append({
                    "resource_type": ALICLOUD_RESOURCE_PATTERNS[resource_type]["resource_type"],
                    "category": ALICLOUD_RESOURCE_PATTERNS[resource_type]["category"],
                    "instance_id": instance_id,
                    "resource_name": f"{ALICLOUD_RESOURCE_PATTERNS[resource_type]['resource_type']} ({instance_id or host})",
                    "region": region,
                    "endpoint": host,
                    "source_env_keys": [cache.get("source_env_key", "")],
                    "source_type": "cache",
                    "source_container": cache.get("container_name", "unknown"),
                    "environment": environment,
                })

    # ─────────────────────────────────────────────────────────────────────────
    # Phase 3: Enrich external dependencies with AliCloud resource details
    # ─────────────────────────────────────────────────────────────────────────
    for ext_dep in external_deps:
        url = ext_dep.get("url", "")
        if not _is_alicloud_resource(url):
            continue

        # Extract hostname from URL
        hostname = url
        if "://" in url:
            hostname = url.split("://", 1)[1].split("/")[0].split(":")[0]

        resource_type = _match_resource_type(hostname, url)
        region = _extract_region_from_hostname(hostname)

        # Determine instance ID based on resource type
        instance_id = None
        resource_name = ext_dep.get("name", "")

        if resource_type:
            prefixes = ALICLOUD_RESOURCE_PATTERNS[resource_type].get("instance_prefixes")
            instance_id = _extract_instance_id_from_hostname(hostname, prefixes)

            # For OSS, extract bucket name
            if resource_type == "oss":
                bucket, region = _extract_bucket_and_region_from_oss(hostname)
                instance_id = bucket
                resource_name = f"OSS Bucket ({bucket})" if bucket else resource_name

            # For SLS, extract project name
            elif resource_type == "sls":
                project, region = _extract_project_and_region_from_sls(hostname)
                instance_id = project
                resource_name = f"SLS Project ({project})" if project else resource_name

            dedup_key = (resource_type, instance_id, region, hostname)
            if dedup_key not in seen:
                seen.add(dedup_key)
                found.append({
                    "resource_type": ALICLOUD_RESOURCE_PATTERNS[resource_type]["resource_type"],
                    "category": ALICLOUD_RESOURCE_PATTERNS[resource_type]["category"],
                    "instance_id": instance_id,
                    "resource_name": resource_name or ALICLOUD_RESOURCE_PATTERNS[resource_type]["resource_type"],
                    "region": region,
                    "endpoint": hostname,
                    "source_env_keys": [ext_dep.get("source_env_key", "")],
                    "source_type": "external_dep",
                    "source_container": ext_dep.get("container_name", "unknown"),
                    "environment": environment,
                })

    # ─────────────────────────────────────────────────────────────────────────
    # Phase 4: Detect AliCloud resources directly from env vars not caught above
    # ─────────────────────────────────────────────────────────────────────────
    for ev in env_vars:
        key = ev.get("key", "")
        value = ev.get("value")
        container_name = ev.get("container_name", "unknown")

        if not value:
            continue

        value_str = str(value).strip()

        # Skip if already processed (part of a database, cache, or external dep)
        if not _is_alicloud_resource(value_str):
            continue

        # Extract hostname
        hostname = value_str
        if "://" in value_str:
            hostname = value_str.split("://", 1)[1].split("/")[0].split(":")[0]

        # Match resource type
        resource_type = _match_resource_type(hostname, value_str)
        if not resource_type:
            continue

        region = _extract_region_from_hostname(hostname)

        # Determine instance ID
        instance_id = None
        resource_name = ""

        prefixes = ALICLOUD_RESOURCE_PATTERNS[resource_type].get("instance_prefixes")
        instance_id = _extract_instance_id_from_hostname(hostname, prefixes)

        # For OSS, extract bucket name
        if resource_type == "oss":
            bucket, region = _extract_bucket_and_region_from_oss(hostname)
            instance_id = bucket
            resource_name = f"OSS Bucket ({bucket})" if bucket else ""

        # For SLS, extract project name
        elif resource_type == "sls":
            project, region = _extract_project_and_region_from_sls(hostname)
            instance_id = project
            resource_name = f"SLS Project ({project})" if project else ""

        if not instance_id:
            instance_id = hostname

        dedup_key = (resource_type, instance_id, region, hostname)
        if dedup_key not in seen:
            seen.add(dedup_key)
            found.append({
                "resource_type": ALICLOUD_RESOURCE_PATTERNS[resource_type]["resource_type"],
                "category": ALICLOUD_RESOURCE_PATTERNS[resource_type]["category"],
                "instance_id": instance_id,
                "resource_name": resource_name or ALICLOUD_RESOURCE_PATTERNS[resource_type]["resource_type"],
                "region": region,
                "endpoint": hostname,
                "source_env_keys": [key],
                "source_type": "direct",
                "source_container": container_name,
                "environment": environment,
            })

    return found


def aggregate_shared_resources(cloud_resources: list[dict]) -> dict[str, list[dict]]:
    """
    Track which AliCloud resource instances are shared across multiple services.

    Groups resources by (instance_id, region, endpoint) to identify shared infrastructure.

    Args:
        cloud_resources: List of CloudResource dicts from detect_cloud_resources()

    Returns:
        Dictionary mapping resource identifier to list of services using it:
        {
            "pgm-prod-db:ap-southeast-1:pgm-prod-db.pg.rds.aliyuncs.com": [
                {"service_name": "auth-service", "container": "auth-app"},
                {"service_name": "user-service", "container": "user-app"},
            ],
            ...
        }
    """
    sharing_map: dict[str, list[dict]] = {}

    for resource in cloud_resources:
        resource_key = f"{resource.get('instance_id')}:{resource.get('region')}:{resource.get('endpoint')}"

        if resource_key not in sharing_map:
            sharing_map[resource_key] = []

        sharing_map[resource_key].append({
            "container": resource.get("source_container"),
            "env_keys": resource.get("source_env_keys"),
            "source_type": resource.get("source_type"),
        })

    return sharing_map


def get_resource_connection_string(
    cloud_resource: dict,
    username: Optional[str] = None,
    password: Optional[str] = None,
    database_name: Optional[str] = None,
    port: Optional[int] = None,
) -> Optional[str]:
    """
    Generate a connection string for a detected AliCloud resource.

    Supports database and cache resources with optional credentials and database name.

    Args:
        cloud_resource: CloudResource dict from detect_cloud_resources()
        username: Optional username for authentication
        password: Optional password for authentication
        database_name: Optional database name (for RDS)
        port: Optional port override

    Returns:
        Connection string, or None if resource type doesn't support connection strings.
        Examples:
            - "postgresql://user:pass@pgm-xxx.pg.rds.aliyuncs.com:5432/dbname"
            - "redis://r-xxx.redis.rds.aliyuncs.com:6379"
    """
    resource_type = cloud_resource.get("resource_type", "")
    endpoint = cloud_resource.get("endpoint")

    if not endpoint:
        return None

    # Map resource types to connection protocols and default ports
    connection_info = {
        "RDS PostgreSQL": ("postgresql", 5432),
        "RDS MySQL": ("mysql", 3306),
        "ApsaraDB MongoDB": ("mongodb", 27017),
        "ApsaraDB Redis": ("redis", 6379),
        "Elasticsearch": ("http", 9200),
    }

    if resource_type not in connection_info:
        return None

    scheme, default_port = connection_info[resource_type]
    actual_port = port or default_port

    # Build connection string based on scheme
    if scheme == "postgresql":
        user_part = f"{username}:{password}@" if username and password else ""
        db_part = f"/{database_name}" if database_name else ""
        return f"postgresql://{user_part}{endpoint}:{actual_port}{db_part}"

    elif scheme == "mysql":
        user_part = f"{username}:{password}@" if username and password else ""
        db_part = f"/{database_name}" if database_name else ""
        return f"mysql+pymysql://{user_part}{endpoint}:{actual_port}{db_part}"

    elif scheme == "mongodb":
        user_part = f"{username}:{password}@" if username and password else ""
        db_part = f"/{database_name}" if database_name else ""
        return f"mongodb://{user_part}{endpoint}:{actual_port}{db_part}"

    elif scheme == "redis":
        password_part = f":{password}@" if password else ""
        return f"redis://{password_part}{endpoint}:{actual_port}"

    elif scheme == "http":
        return f"http://{endpoint}:{actual_port}"

    return None
