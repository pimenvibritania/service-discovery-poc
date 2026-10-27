"""Scan K8s Service objects to build a registry of all known services per namespace."""

from typing import Any


def _get(obj, *keys, default=None):
    for k in keys:
        if obj is None:
            return default
        obj = obj.get(k) if isinstance(obj, dict) else getattr(obj, k, None)
    return obj if obj is not None else default


def scan_services(client, namespace: str) -> dict[str, dict]:
    """
    Returns {service_name: {namespace, type, ports, selectors, external_ips}} for all K8s Services.
    Used to validate inter-service dependency targets.
    """
    service_registry: dict[str, dict] = {}
    services = client.list_services(namespace)

    for svc in services:
        if isinstance(svc, dict):
            name = _get(svc, "metadata", "name") or ""
            ns = _get(svc, "metadata", "namespace") or namespace
            spec = svc.get("spec") or {}
            svc_type = spec.get("type", "ClusterIP")
            selectors = spec.get("selector") or {}
            ports_raw = spec.get("ports") or []
            cluster_ip = spec.get("clusterIP")
            external_ips = spec.get("externalIPs") or []
            lb_ingress = _get(svc, "status", "loadBalancer", "ingress") or []
        else:
            name = _get(svc, "metadata", "name") or ""
            ns = _get(svc, "metadata", "namespace") or namespace
            svc_type = _get(svc, "spec", "type") or "ClusterIP"
            selectors = _get(svc, "spec", "selector") or {}
            ports_raw = _get(svc, "spec", "ports") or []
            cluster_ip = _get(svc, "spec", "cluster_ip")
            external_ips = _get(svc, "spec", "external_i_ps") or []
            lb_ingress = _get(svc, "status", "load_balancer", "ingress") or []

        ports = []
        for p in ports_raw:
            if isinstance(p, dict):
                ports.append({
                    "name": p.get("name"),
                    "port": p.get("port"),
                    "target_port": p.get("targetPort"),
                    "protocol": p.get("protocol", "TCP"),
                })
            else:
                ports.append({
                    "name": getattr(p, "name", None),
                    "port": getattr(p, "port", None),
                    "target_port": getattr(p, "target_port", None),
                    "protocol": getattr(p, "protocol", "TCP"),
                })

        lb_endpoints = []
        for ing in lb_ingress:
            if isinstance(ing, dict):
                lb_endpoints.append(ing.get("ip") or ing.get("hostname"))
            else:
                lb_endpoints.append(getattr(ing, "ip", None) or getattr(ing, "hostname", None))

        if name:
            service_registry[name] = {
                "name": name,
                "namespace": ns,
                "type": svc_type,
                "selectors": dict(selectors),
                "ports": ports,
                "cluster_ip": cluster_ip,
                "external_ips": external_ips,
                "lb_endpoints": [e for e in lb_endpoints if e],
            }

    return service_registry
