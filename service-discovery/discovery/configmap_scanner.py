"""Scan ConfigMaps and build a lookup table: {configmap_name: {key: value}}."""

from typing import Any


def _get(obj, *keys, default=None):
    for k in keys:
        if obj is None:
            return default
        obj = obj.get(k) if isinstance(obj, dict) else getattr(obj, k, None)
    return obj if obj is not None else default


def scan_configmaps(client, namespace: str) -> dict[str, dict[str, str]]:
    """
    Returns a dict mapping configmap_name -> {key: value} for all ConfigMaps in the namespace.
    Used by deployment_scanner to resolve valueFrom.configMapKeyRef references.
    """
    configmap_data: dict[str, dict[str, str]] = {}
    configmaps = client.list_configmaps(namespace)

    for cm in configmaps:
        if isinstance(cm, dict):
            name = _get(cm, "metadata", "name") or ""
            data = cm.get("data") or {}
        else:
            name = _get(cm, "metadata", "name") or ""
            data = getattr(cm, "data", None) or {}

        if name and data:
            configmap_data[name] = dict(data)

    return configmap_data
