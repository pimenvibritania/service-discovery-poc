"""
Detect inter-service dependencies via kube DNS patterns and env var key heuristics.
"""

import re
from typing import Optional


# Kube DNS patterns
KUBE_DNS_PATTERNS = [
    # Full form: service-name.namespace.svc.cluster.local
    re.compile(r"(?P<service>[a-z0-9][a-z0-9\-]+)\.(?P<namespace>[a-z0-9][a-z0-9\-]+)\.svc\.cluster\.local(?::(?P<port>\d+))?"),
    # Short form: service-name.namespace.svc
    re.compile(r"(?P<service>[a-z0-9][a-z0-9\-]+)\.(?P<namespace>[a-z0-9][a-z0-9\-]+)\.svc(?::(?P<port>\d+))?"),
    # Medium form: service-name.namespace (no .svc suffix)
    re.compile(r"^(?P<service>[a-z0-9][a-z0-9\-]+)\.(?P<namespace>[a-z0-9][a-z0-9\-]+)$"),
]

# Env key patterns that suggest a service dependency
SERVICE_ENV_KEY_PATTERNS = [
    re.compile(r"(?i)([A-Z][A-Z0-9_]+)_(HOST|HOSTNAME|URL|URI|ENDPOINT|BASE_URL|API_URL|SERVICE_URL|ADDR|ADDRESS|SVC_HOST)$"),
    re.compile(r"(?i)([A-Z][A-Z0-9_]+)_(GRPC_HOST|GRPC_ADDR|GRPC_ENDPOINT)$"),
    re.compile(r"(?i)^([A-Z][A-Z0-9_]+)_(SERVICE|SVC)_(HOST|URL|ENDPOINT)$"),
]

# Prefixes that indicate a non-service connection (DB, cache, external)
EXCLUDE_PREFIXES = re.compile(
    r"(?i)(postgres|pgsql|pg|mongo|redis|mysql|elastic|memcache|rabbit|kafka|"
    r"aws|alibaba|aliyun|oss|cdn|smtp|mail|twilio|stripe|sendgrid|firebase)"
)


def _extract_protocol(key: str, value: str) -> str:
    """Infer protocol from key name or URL scheme."""
    k = key.lower()
    v = (value or "").lower()
    if "grpc" in k:
        return "grpc"
    if v.startswith("grpc://"):
        return "grpc"
    if v.startswith("https://"):
        return "https"
    return "http"


def _match_kube_dns(value: str) -> Optional[re.Match]:
    """Try to match any kube DNS pattern against a value."""
    for pattern in KUBE_DNS_PATTERNS:
        m = pattern.search(value)
        if m:
            return m
    return None


def detect_service_dependencies(
    env_vars: list[dict],
    known_services: set[str],
    classified_keys: set,
) -> list[dict]:
    """
    Detect inter-service dependencies.
    Returns list of ServiceDependency dicts.
    """
    found = []
    seen: set[str] = set()

    for ev in env_vars:
        key = ev.get("key", "")
        value = ev.get("value")
        source_type = ev.get("source_type", "direct")
        container_name = ev.get("container_name", "unknown")

        if not value or source_type == "secret":
            continue

        value_str = str(value).strip()

        # Try kube DNS match first (most reliable)
        match = _match_kube_dns(value_str)
        if match:
            service = match.group("service")
            namespace = match.group("namespace")
            dedup = f"{service}.{namespace}"
            if dedup not in seen:
                seen.add(dedup)
                validated = service in known_services
                found.append({
                    "target_service": service,
                    "target_namespace": namespace,
                    "protocol": _extract_protocol(key, value_str),
                    "source_env_key": key,
                    "discovered_url": value_str,
                    "validated": validated,
                    "container_name": container_name,
                })
            classified_keys.add(key)
            continue

        # Heuristic: env key looks like a service pointer
        key_matched = any(p.search(key) for p in SERVICE_ENV_KEY_PATTERNS)
        if not key_matched:
            continue

        # Skip if the key prefix suggests a DB/cache/external connection
        if EXCLUDE_PREFIXES.search(key):
            continue

        # Skip if value is clearly not a hostname/URL
        if not _looks_like_hostname(value_str):
            continue

        # Skip known DB/external patterns
        if _looks_like_external(value_str):
            continue

        # Try to infer service name from the env key
        # e.g., AUTH_SERVICE_HOST -> auth-service, BILLING_URL -> billing
        svc_name = _infer_service_name(key)
        if not svc_name:
            continue

        # Validate against known services (partial match)
        target_ns = "unknown"
        validated = False
        for known in known_services:
            if svc_name in known or known.startswith(svc_name.rstrip("-")):
                svc_name = known
                validated = True
                break

        dedup = f"{svc_name}.{target_ns}"
        if dedup in seen:
            classified_keys.add(key)
            continue
        seen.add(dedup)

        found.append({
            "target_service": svc_name,
            "target_namespace": target_ns,
            "protocol": _extract_protocol(key, value_str),
            "source_env_key": key,
            "discovered_url": value_str,
            "validated": validated,
            "container_name": container_name,
        })
        classified_keys.add(key)

    return found


def _looks_like_hostname(value: str) -> bool:
    """Heuristic: does this value look like a hostname or URL?"""
    if not value or len(value) < 3:
        return False
    v = value.lower()
    # IP address
    if re.match(r"^\d+\.\d+\.\d+\.\d+(:\d+)?$", v):
        return True
    # Has scheme
    if "://" in v:
        return True
    # Has dots (domain) or no dots (short service name in same namespace)
    return "." in v or re.match(r"^[a-z0-9][a-z0-9\-]+$", v) is not None


def _looks_like_external(value: str) -> bool:
    """Heuristic: does this value look like an external endpoint (not an internal svc)?"""
    external_domains = [
        ".aliyuncs.com", ".amazonaws.com", ".googleapis.com",
        ".cloudflare.com", ".akamai.com", ".fastly.net",
        "api.stripe.com", "api.twilio.com", "smtp.", ".sendgrid.",
        "github.com", "gitlab.com",
    ]
    v = value.lower()
    return any(d in v for d in external_domains)


def _infer_service_name(key: str) -> Optional[str]:
    """
    Attempt to derive a service name from an env var key.
    e.g., AUTH_SERVICE_HOST -> auth-service
         BILLING_API_URL -> billing-api
         NOCODE_SVC_ENDPOINT -> nocode
    """
    # Remove common suffix patterns
    key = re.sub(r"_(HOST|HOSTNAME|URL|URI|ENDPOINT|BASE_URL|API_URL|SERVICE_URL|ADDR|ADDRESS|SVC_HOST|GRPC_HOST|GRPC_ADDR|GRPC_ENDPOINT)$",
                 "", key, flags=re.IGNORECASE)
    # Remove INTERNAL_, EXTERNAL_ prefixes
    key = re.sub(r"^(INTERNAL_|EXTERNAL_|INT_|EXT_)", "", key, flags=re.IGNORECASE)
    # Convert underscores to hyphens and lowercase
    name = key.lower().replace("_", "-")
    # Remove trailing -svc, -service suffixes that we'll re-add
    name = re.sub(r"-(svc|service)$", "", name)
    if not name or len(name) < 2:
        return None
    return name
