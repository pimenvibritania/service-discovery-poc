"""
Scan Secrets — metadata only. NEVER read or store actual secret values.
Returns: {secret_name: [key1, key2, ...]} — just key names, not values.
"""


def _get(obj, *keys, default=None):
    for k in keys:
        if obj is None:
            return default
        obj = obj.get(k) if isinstance(obj, dict) else getattr(obj, k, None)
    return obj if obj is not None else default


def scan_secrets(client, namespace: str) -> dict[str, list[str]]:
    """
    Returns a dict mapping secret_name -> [list of key names].
    Values are intentionally never read or stored.
    """
    secret_keys: dict[str, list[str]] = {}
    secrets = client.list_secrets(namespace)

    for secret in secrets:
        if isinstance(secret, dict):
            name = _get(secret, "metadata", "name") or ""
            # 'data' contains base64-encoded values — we only want the keys
            data = secret.get("data") or {}
            string_data = secret.get("stringData") or {}
        else:
            name = _get(secret, "metadata", "name") or ""
            data = getattr(secret, "data", None) or {}
            string_data = getattr(secret, "string_data", None) or {}

        if name:
            keys = list(data.keys()) + list(string_data.keys())
            secret_keys[name] = keys

    return secret_keys
