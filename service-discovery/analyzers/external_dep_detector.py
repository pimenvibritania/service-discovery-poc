"""
Detect external API/service dependencies from env var values.
Any URL/hostname that isn't kube DNS and isn't a known DB is classified as external.
"""

import re
from typing import Optional


EXTERNAL_PATTERNS: dict[str, list[re.Pattern]] = {
    "alibaba_cloud": [
        re.compile(r"\.aliyuncs\.com", re.IGNORECASE),
        re.compile(r"\.alibaba\.com", re.IGNORECASE),
        re.compile(r"\.alicdn\.com", re.IGNORECASE),
        re.compile(r"\.alipay\.com", re.IGNORECASE),
    ],
    "aws": [
        re.compile(r"\.amazonaws\.com", re.IGNORECASE),
        re.compile(r"\.aws\.amazon\.com", re.IGNORECASE),
        re.compile(r"\.cloudfront\.net", re.IGNORECASE),
    ],
    "gcp": [
        re.compile(r"\.googleapis\.com", re.IGNORECASE),
        re.compile(r"\.googleusercontent\.com", re.IGNORECASE),
        re.compile(r"storage\.cloud\.google\.com", re.IGNORECASE),
    ],
    "stripe": [re.compile(r"stripe\.com", re.IGNORECASE)],
    "twilio": [re.compile(r"twilio\.com", re.IGNORECASE)],
    "sendgrid": [re.compile(r"sendgrid\.(net|com)", re.IGNORECASE)],
    "firebase": [re.compile(r"firebase(io|app)?\.com", re.IGNORECASE)],
    "datadog": [re.compile(r"datadoghq\.com", re.IGNORECASE)],
    "sentry": [re.compile(r"sentry\.io", re.IGNORECASE)],
    "newrelic": [re.compile(r"newrelic\.com", re.IGNORECASE)],
    "github": [re.compile(r"github\.com", re.IGNORECASE)],
    "gitlab": [re.compile(r"gitlab\.com", re.IGNORECASE)],
    "cdn_storage": [
        re.compile(r"\.oss[-\.]", re.IGNORECASE),
        re.compile(r"\.cdn\.", re.IGNORECASE),
        re.compile(r"cloudflare\.com", re.IGNORECASE),
        re.compile(r"fastly\.net", re.IGNORECASE),
        re.compile(r"akamai(edge)?\.net", re.IGNORECASE),
    ],
    "smtp_email": [
        re.compile(r"smtp\.", re.IGNORECASE),
        re.compile(r"\.mailgun\.", re.IGNORECASE),
        re.compile(r"ses\.amazonaws\.com", re.IGNORECASE),
    ],
    "third_party_api": [
        re.compile(r"^https?://api\.", re.IGNORECASE),
        re.compile(r"\.io/api", re.IGNORECASE),
    ],
}

# Kube DNS / internal patterns to skip
KUBE_DNS_RE = re.compile(r"\.svc\.cluster\.local|\.svc\b")
INTERNAL_IP_RE = re.compile(r"^(10\.|172\.(1[6-9]|2\d|3[01])\.|192\.168\.)\d+\.\d+")

# Env key patterns that suggest an external connection
EXTERNAL_KEY_PATTERNS = [
    re.compile(r"(?i)(endpoint|url|uri|host|hostname|addr|server)$"),
    re.compile(r"(?i)_(api|service|svc|sdk)_(url|host|endpoint|key|token)$"),
]


def _categorize(value: str) -> Optional[str]:
    """Return the category name for an external URL, or None if it's not external."""
    for category, patterns in EXTERNAL_PATTERNS.items():
        for p in patterns:
            if p.search(value):
                return category
    return None


def _looks_like_url(value: str) -> bool:
    return bool(value and ("://" in value or ("." in value and len(value) > 6)))


def _is_internal(value: str) -> bool:
    return bool(KUBE_DNS_RE.search(value) or INTERNAL_IP_RE.match(value))


def _friendly_name(category: str, url: str) -> str:
    """Generate a human-readable name for the dependency."""
    category_names = {
        "alibaba_cloud": "Alibaba Cloud",
        "aws": "AWS",
        "gcp": "Google Cloud",
        "stripe": "Stripe",
        "twilio": "Twilio",
        "sendgrid": "SendGrid",
        "firebase": "Firebase",
        "datadog": "Datadog",
        "sentry": "Sentry",
        "newrelic": "New Relic",
        "github": "GitHub",
        "gitlab": "GitLab",
        "cdn_storage": "CDN/Storage",
        "smtp_email": "SMTP/Email",
        "third_party_api": "Third-party API",
    }
    base = category_names.get(category, category.replace("_", " ").title())
    # Try to extract a hostname for the suffix
    try:
        if "://" in url:
            host = url.split("://", 1)[1].split("/")[0].split(":")[0]
        else:
            host = url.split("/")[0].split(":")[0]
        # Use subdomain as hint
        parts = host.split(".")
        if len(parts) >= 2:
            subdomain = parts[0]
            if subdomain not in ("api", "www", "s3", "smtp"):
                return f"{base} ({subdomain})"
    except Exception:
        pass
    return base


def detect_external_dependencies(env_vars: list[dict], classified_keys: set) -> list[dict]:
    """Detect external API/service dependencies. Returns list of ExternalDependency dicts."""
    found = []
    seen: set[str] = set()

    for ev in env_vars:
        key = ev.get("key", "")
        value = ev.get("value")
        source_type = ev.get("source_type", "direct")
        container_name = ev.get("container_name", "unknown")

        if not value or source_type == "secret":
            continue
        if key in classified_keys:
            continue

        value_str = str(value).strip()
        if not _looks_like_url(value_str):
            continue
        if _is_internal(value_str):
            continue

        category = _categorize(value_str)
        if not category:
            # Check if the env key suggests this is an external pointer
            key_looks_external = any(p.search(key) for p in EXTERNAL_KEY_PATTERNS)
            if not key_looks_external:
                continue
            category = "third_party_api"

        dedup = f"{category}:{value_str}"
        if dedup in seen:
            classified_keys.add(key)
            continue
        seen.add(dedup)

        found.append({
            "name": _friendly_name(category, value_str),
            "url": value_str,
            "category": category,
            "source_env_key": key,
            "container_name": container_name,
        })
        classified_keys.add(key)

    return found
