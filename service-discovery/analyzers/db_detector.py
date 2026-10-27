"""
Detect database and cache connections from environment variables.
Uses pattern registry — add new patterns here as new DB types are discovered.
"""

import re
from typing import Optional


# ─────────────────────────────────────────────────────────────────────────────
# Pattern Registry
# ─────────────────────────────────────────────────────────────────────────────

DB_PATTERNS = {
    "postgresql": {
        "env_keys": [
            r"(?i)(postgres|pgsql|pg)_(host|hostname|server|url|uri|addr|endpoint)",
            r"(?i)database_(host|url|uri)",
            r"(?i)^db_(host|url|server|uri|endpoint)$",
            r"(?i)rds_(host|endpoint)",
        ],
        "url_schemes": ["postgres://", "postgresql://"],
        "port_default": 5432,
        "user_keys": [r"(?i)(postgres|pgsql|pg|db|database)_(user|username)"],
        "database_keys": [r"(?i)(postgres|pgsql|pg|db)_(database|dbname|name|db)$"],
        "port_keys": [r"(?i)(postgres|pgsql|pg|db|database)_(port)$"],
        "instance_prefix": "rm-",  # Alibaba RDS PostgreSQL
    },
    "mongodb": {
        "env_keys": [
            r"(?i)mongo(db)?_(host|hostname|server|url|uri|addr|endpoint)",
            r"(?i)^mongodb_uri$",
            r"(?i)^mongo_uri$",
        ],
        "url_schemes": ["mongodb://", "mongodb+srv://"],
        "port_default": 27017,
        "user_keys": [r"(?i)mongo(db)?_(user|username)"],
        "database_keys": [r"(?i)mongo(db)?_(database|db|dbname|name)$"],
        "port_keys": [r"(?i)mongo(db)?_(port)$"],
        "instance_prefix": "dds-",  # Alibaba RDS MongoDB
    },
    "mysql": {
        "env_keys": [
            r"(?i)mysql_(host|hostname|server|url|uri|addr|endpoint)",
            r"(?i)^mysql_host$",
        ],
        "url_schemes": ["mysql://", "mysql+pymysql://", "mysql+mysqlconnector://"],
        "port_default": 3306,
        "user_keys": [r"(?i)mysql_(user|username)"],
        "database_keys": [r"(?i)mysql_(database|db|dbname|name)$"],
        "port_keys": [r"(?i)mysql_(port)$"],
        "instance_prefix": "rm-",
    },
    "elasticsearch": {
        "env_keys": [
            r"(?i)^(elasticsearch|elastic|es)_(host|hostname|url|uri|addr|endpoint)$",
            r"(?i)^elastic_url$",
            r"(?i)^es_url$",
        ],
        "url_schemes": [],  # matched via content heuristic
        "port_default": 9200,
        "user_keys": [r"(?i)(elasticsearch|elastic|es)_(user|username)"],
        "database_keys": [],
        "port_keys": [r"(?i)(elasticsearch|elastic|es)_(port)$"],
        "instance_prefix": None,
    },
}

CACHE_PATTERNS = {
    "redis": {
        "env_keys": [
            r"(?i)redis_(host|hostname|server|url|uri|addr|endpoint)",
            r"(?i)^redis_url$",
            r"(?i)cache_(host|url|uri|addr)",
        ],
        "url_schemes": ["redis://", "rediss://"],
        "port_default": 6379,
        "port_keys": [r"(?i)redis_(port)$"],
        "instance_prefix": "r-",  # Alibaba Redis
    },
    "memcached": {
        "env_keys": [
            r"(?i)memcache(d)?_(host|hostname|server|url|uri|addr)",
        ],
        "url_schemes": ["memcached://"],
        "port_default": 11211,
        "port_keys": [r"(?i)memcache(d)?_(port)$"],
        "instance_prefix": None,
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _matches_any(key: str, patterns: list[str]) -> bool:
    return any(re.search(p, key) for p in patterns)


def _parse_instance_id(host: str, prefix: Optional[str]) -> Optional[str]:
    """Extract Alibaba Cloud instance ID from hostname (e.g., 'rm-abcde' from 'rm-abcde.pg.rds.aliyuncs.com')."""
    if not host or not prefix:
        return None
    parts = host.split(".")
    if parts and parts[0].startswith(prefix):
        return parts[0]
    return None


def _parse_url(url: str, db_type: str) -> dict:
    """Parse a DB URL into {host, port, user, database_name}."""
    result = {"host": None, "port": None, "user": None, "database_name": None}
    try:
        # Remove scheme
        schemes = {
            "postgresql": ["postgresql://", "postgres://"],
            "mongodb": ["mongodb+srv://", "mongodb://"],
            "mysql": ["mysql+pymysql://", "mysql+mysqlconnector://", "mysql://"],
            "redis": ["rediss://", "redis://"],
            "elasticsearch": ["https://", "http://"],
            "memcached": ["memcached://"],
        }
        rest = url
        for scheme in schemes.get(db_type, []):
            if url.lower().startswith(scheme):
                rest = url[len(scheme):]
                break

        # user:pass@host:port/db
        if "@" in rest:
            userinfo, hostpart = rest.rsplit("@", 1)
            if ":" in userinfo:
                result["user"] = userinfo.split(":")[0]
            else:
                result["user"] = userinfo
        else:
            hostpart = rest

        # Strip query string
        hostpart = hostpart.split("?")[0]

        # Split host/db
        if "/" in hostpart:
            hostdb, dbname = hostpart.split("/", 1)
            result["database_name"] = dbname or None
        else:
            hostdb = hostpart

        # Split host:port
        if ":" in hostdb and not hostdb.startswith("["):
            h, p = hostdb.rsplit(":", 1)
            result["host"] = h
            try:
                result["port"] = int(p)
            except ValueError:
                pass
        else:
            result["host"] = hostdb
    except Exception:
        pass
    return result


def _find_related_value(env_vars: list[dict], patterns: list[str], exclude_key: str = "") -> Optional[str]:
    """Find the value of an env var matching any of the given patterns."""
    for ev in env_vars:
        k = ev.get("key", "")
        v = ev.get("value")
        if k != exclude_key and v and _matches_any(k, patterns):
            return str(v)
    return None


def _is_db_connection_value(value: str, db_type_info: dict) -> bool:
    """Check if a value looks like a DB connection string."""
    if not value:
        return False
    v = value.lower()
    for scheme in db_type_info.get("url_schemes", []):
        if v.startswith(scheme.lower()):
            return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Main detectors
# ─────────────────────────────────────────────────────────────────────────────

def detect_databases(env_vars: list[dict], environment: str, classified_keys: set) -> list[dict]:
    """Detect database connections from env vars. Returns list of DatabaseConnection dicts."""
    found = []
    seen_hosts: set[str] = set()

    for db_type, pattern_info in DB_PATTERNS.items():
        for ev in env_vars:
            key = ev.get("key", "")
            value = ev.get("value")
            source_type = ev.get("source_type", "direct")
            source_name = ev.get("source_name")
            container_name = ev.get("container_name", "unknown")

            # Match by key pattern
            matched_by_key = _matches_any(key, pattern_info["env_keys"])
            # Match by URL scheme in value
            matched_by_url = value and _is_db_connection_value(str(value), pattern_info)

            if not (matched_by_key or matched_by_url):
                continue

            # Resolve host and port
            host = None
            port = pattern_info["port_default"]
            user = None
            database_name = None

            if value and _is_db_connection_value(str(value), pattern_info):
                parsed = _parse_url(str(value), db_type)
                host = parsed.get("host")
                port = parsed.get("port") or port
                user = parsed.get("user")
                database_name = parsed.get("database_name")
            elif value:
                host = str(value).strip()

            if not host:
                continue

            # Avoid duplicates (same host/type)
            dedup_key = f"{db_type}:{host}"
            if dedup_key in seen_hosts:
                classified_keys.add(key)
                continue
            seen_hosts.add(dedup_key)

            # Look for companion env vars (user, db name, port)
            if not user:
                user = _find_related_value(env_vars, pattern_info.get("user_keys", []), key)
            if not database_name:
                database_name = _find_related_value(env_vars, pattern_info.get("database_keys", []), key)
            port_val = _find_related_value(env_vars, pattern_info.get("port_keys", []), key)
            if port_val:
                try:
                    port = int(port_val)
                except ValueError:
                    pass

            instance_id = _parse_instance_id(host, pattern_info.get("instance_prefix"))

            found.append({
                "db_type": db_type,
                "host": host,
                "port": port,
                "user": user,
                "database_name": database_name,
                "instance_id": instance_id,
                "source_env_key": key,
                "source_type": source_type,
                "source_name": source_name,
                "environment": environment,
                "container_name": container_name,
            })
            classified_keys.add(key)

    return found


def detect_caches(env_vars: list[dict], environment: str, classified_keys: set) -> list[dict]:
    """Detect cache connections (Redis, Memcached) from env vars."""
    found = []
    seen_hosts: set[str] = set()

    for cache_type, pattern_info in CACHE_PATTERNS.items():
        for ev in env_vars:
            key = ev.get("key", "")
            value = ev.get("value")
            source_type = ev.get("source_type", "direct")
            source_name = ev.get("source_name")
            container_name = ev.get("container_name", "unknown")

            matched_by_key = _matches_any(key, pattern_info["env_keys"])
            matched_by_url = value and _is_db_connection_value(str(value), pattern_info)

            if not (matched_by_key or matched_by_url):
                continue

            host = None
            port = pattern_info["port_default"]

            if value and _is_db_connection_value(str(value), pattern_info):
                parsed = _parse_url(str(value), cache_type)
                host = parsed.get("host")
                port = parsed.get("port") or port
            elif value:
                host = str(value).strip()

            if not host:
                continue

            dedup_key = f"{cache_type}:{host}"
            if dedup_key in seen_hosts:
                classified_keys.add(key)
                continue
            seen_hosts.add(dedup_key)

            port_val = _find_related_value(env_vars, pattern_info.get("port_keys", []), key)
            if port_val:
                try:
                    port = int(port_val)
                except ValueError:
                    pass

            found.append({
                "cache_type": cache_type,
                "host": host,
                "port": port,
                "source_env_key": key,
                "source_type": source_type,
                "source_name": source_name,
                "environment": environment,
                "container_name": container_name,
            })
            classified_keys.add(key)

    return found
