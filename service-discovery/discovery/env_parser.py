"""
Core intelligence: parse raw env vars into structured dependency information.
Delegates to db_detector, service_dep_detector, and external_dep_detector.
"""

from analyzers.db_detector import detect_databases, detect_caches
from analyzers.service_dep_detector import detect_service_dependencies
from analyzers.external_dep_detector import detect_external_dependencies


def parse_env_vars(
    workload: dict,
    environment: str,
    known_services: set[str],
) -> dict:
    """
    Takes a raw workload dict (from deployment_scanner) and returns structured dependency info.

    Args:
        workload: Raw workload dict with env_vars list
        environment: "staging" or "production"
        known_services: Set of known K8s service names (for dependency validation)

    Returns:
        Structured dict with databases, service_dependencies, external_dependencies, cache_connections
    """
    env_vars = workload.get("env_vars", [])
    classified_keys: set[str] = set()

    databases = detect_databases(env_vars, environment, classified_keys)
    caches = detect_caches(env_vars, environment, classified_keys)
    service_deps = detect_service_dependencies(env_vars, known_services, classified_keys)
    external_deps = detect_external_dependencies(env_vars, classified_keys)

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
        "raw_env_count": len(env_vars),
        "unclassified_env_keys": unclassified,
    }


def _looks_like_url_or_host(value: str) -> bool:
    """Heuristic: does this value look like a hostname/URL that we might have missed?"""
    if not value or len(value) < 4:
        return False
    indicators = ["://", ".svc.", ".cluster.local", ".aliyuncs.com", ".amazonaws.com",
                  ".internal", "host", "endpoint", "addr"]
    v = value.lower()
    return any(ind in v for ind in indicators)
