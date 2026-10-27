"""Scan Deployments and StatefulSets, extracting env vars, container metadata, and resource specs."""

from typing import Any
from rich.console import Console

console = Console()


def _get_attr(obj, *attrs, default=None):
    """Safely traverse nested attributes or dict keys."""
    for attr in attrs:
        if obj is None:
            return default
        if isinstance(obj, dict):
            obj = obj.get(attr)
        else:
            obj = getattr(obj, attr, None)
    return obj if obj is not None else default


def _extract_resources(container) -> dict:
    """Extract CPU/memory requests and limits from a container spec."""
    resources = {}
    req = _get_attr(container, "resources", "requests") or {}
    lim = _get_attr(container, "resources", "limits") or {}
    if isinstance(req, dict):
        resources["cpu_request"] = req.get("cpu")
        resources["memory_request"] = req.get("memory")
    else:
        resources["cpu_request"] = _get_attr(req, "cpu")
        resources["memory_request"] = _get_attr(req, "memory")
    if isinstance(lim, dict):
        resources["cpu_limit"] = lim.get("cpu")
        resources["memory_limit"] = lim.get("memory")
    else:
        resources["cpu_limit"] = _get_attr(lim, "cpu")
        resources["memory_limit"] = _get_attr(lim, "memory")
    return resources


def _extract_health_checks(container) -> list[dict]:
    checks = []
    # Support both snake_case (live K8s object) and camelCase (fixture dict)
    probe_names = [
        ("liveness",  "liveness_probe",  "livenessProbe"),
        ("readiness", "readiness_probe",  "readinessProbe"),
        ("startup",   "startup_probe",    "startupProbe"),
    ]
    for label, snake, camel in probe_names:
        probe = _get_attr(container, snake) or (
            container.get(camel) if isinstance(container, dict) else None
        )
        if probe is None:
            continue
        entry = {"check_type": label, "path": None, "port": 0, "scheme": "HTTP"}
        # httpGet — support both camelCase and snake_case
        http = (
            (probe.get("httpGet") or probe.get("http_get"))
            if isinstance(probe, dict)
            else (_get_attr(probe, "http_get") or _get_attr(probe, "httpGet"))
        )
        if http:
            entry["path"] = (http.get("path") if isinstance(http, dict) else _get_attr(http, "path")) or "/"
            port_val = (http.get("port") if isinstance(http, dict) else _get_attr(http, "port")) or 0
            entry["port"] = int(port_val)
            scheme_val = (http.get("scheme") if isinstance(http, dict) else _get_attr(http, "scheme")) or "HTTP"
            entry["scheme"] = scheme_val
        # tcpSocket
        tcp = (
            (probe.get("tcpSocket") or probe.get("tcp_socket"))
            if isinstance(probe, dict)
            else (_get_attr(probe, "tcp_socket") or _get_attr(probe, "tcpSocket"))
        )
        if tcp:
            entry["scheme"] = "TCP"
            port_val = (tcp.get("port") if isinstance(tcp, dict) else _get_attr(tcp, "port")) or 0
            entry["port"] = int(port_val)
        checks.append(entry)
    return checks


def _extract_ports(container) -> list[int]:
    ports = _get_attr(container, "ports") or []
    result = []
    for p in ports:
        cp = _get_attr(p, "container_port") or _get_attr(p, "containerPort")
        if cp:
            result.append(int(cp))
    return result


def _extract_env_vars(container, configmap_data: dict, secret_keys: dict) -> list[dict]:
    """
    Extract all env vars from a container, resolving ConfigMapKeyRef and SecretKeyRef references.
    Returns list of dicts: {key, value, source_type, source_name, container_name}
    """
    container_name = _get_attr(container, "name") or "unknown"
    env_list = _get_attr(container, "env") or []
    result = []

    for env in env_list:
        key = _get_attr(env, "name") or ""
        value_from = _get_attr(env, "value_from") or _get_attr(env, "valueFrom")
        direct_value = _get_attr(env, "value")

        if value_from:
            cm_ref = _get_attr(value_from, "config_map_key_ref") or _get_attr(value_from, "configMapKeyRef")
            sec_ref = _get_attr(value_from, "secret_key_ref") or _get_attr(value_from, "secretKeyRef")

            if cm_ref:
                cm_name = _get_attr(cm_ref, "name") or ""
                cm_key = _get_attr(cm_ref, "key") or ""
                resolved = configmap_data.get(cm_name, {}).get(cm_key)
                result.append({
                    "key": key,
                    "value": resolved,
                    "source_type": "configmap",
                    "source_name": cm_name,
                    "container_name": container_name,
                })
            elif sec_ref:
                sec_name = _get_attr(sec_ref, "name") or ""
                sec_key = _get_attr(sec_ref, "key") or ""
                result.append({
                    "key": key,
                    "value": None,  # Never read actual secret values
                    "source_type": "secret",
                    "source_name": sec_name,
                    "secret_key": sec_key,
                    "container_name": container_name,
                })
        else:
            result.append({
                "key": key,
                "value": direct_value,
                "source_type": "direct",
                "source_name": None,
                "container_name": container_name,
            })

    # Process envFrom (bulk ConfigMap/Secret mounts)
    env_from = _get_attr(container, "env_from") or _get_attr(container, "envFrom") or []
    for ef in env_from:
        cm_ref = _get_attr(ef, "config_map_ref") or _get_attr(ef, "configMapRef")
        sec_ref = _get_attr(ef, "secret_ref") or _get_attr(ef, "secretRef")
        prefix = _get_attr(ef, "prefix") or ""

        if cm_ref:
            cm_name = _get_attr(cm_ref, "name") or ""
            for k, v in configmap_data.get(cm_name, {}).items():
                result.append({
                    "key": prefix + k,
                    "value": v,
                    "source_type": "configmap",
                    "source_name": cm_name,
                    "container_name": container_name,
                })
        elif sec_ref:
            sec_name = _get_attr(sec_ref, "name") or ""
            for k in secret_keys.get(sec_name, []):
                result.append({
                    "key": prefix + k,
                    "value": None,
                    "source_type": "secret",
                    "source_name": sec_name,
                    "secret_key": k,
                    "container_name": container_name,
                })

    return result


def scan_workloads(client, namespace: str, configmap_data: dict, secret_keys: dict,
                   scan_deployments: bool = True, scan_statefulsets: bool = True) -> list[dict]:
    """
    Scan Deployments and StatefulSets in a namespace.
    Returns a list of raw workload dicts ready for the env parser.
    """
    workloads = []

    if scan_deployments:
        deployments = client.list_deployments(namespace)
        for dep in deployments:
            w = _extract_workload(dep, namespace, configmap_data, secret_keys, "Deployment")
            if w:
                workloads.append(w)

    if scan_statefulsets:
        statefulsets = client.list_statefulsets(namespace)
        for sts in statefulsets:
            w = _extract_workload(sts, namespace, configmap_data, secret_keys, "StatefulSet")
            if w:
                workloads.append(w)

    return workloads


def _extract_workload(resource, namespace: str, configmap_data: dict, secret_keys: dict, kind: str) -> dict:
    """Extract metadata from a single workload resource (live K8s object or fixture dict)."""
    is_dict = isinstance(resource, dict)

    def g(*attrs):
        return _get_attr(resource, *attrs)

    if is_dict:
        name = resource.get("metadata", {}).get("name", "")
        labels = resource.get("metadata", {}).get("labels", {}) or {}
        annotations = resource.get("metadata", {}).get("annotations", {}) or {}
        spec = resource.get("spec", {}) or {}
        replicas = spec.get("replicas", 1)
        template = spec.get("template", {}) or {}
        pod_spec = template.get("spec", {}) or {}
        containers = pod_spec.get("containers", []) or []
        init_containers = pod_spec.get("initContainers", []) or []
    else:
        name = g("metadata", "name") or ""
        labels = g("metadata", "labels") or {}
        annotations = g("metadata", "annotations") or {}
        replicas = g("spec", "replicas") or 1
        containers = g("spec", "template", "spec", "containers") or []
        init_containers = g("spec", "template", "spec", "init_containers") or []

    if not name:
        return None

    all_env_vars = []
    images = []
    all_ports = []
    all_health_checks = []
    cpu_req = cpu_lim = mem_req = mem_lim = None

    for container in (containers + init_containers):
        env_vars = _extract_env_vars(container, configmap_data, secret_keys)
        all_env_vars.extend(env_vars)

        if is_dict:
            img = container.get("image", "")
        else:
            img = _get_attr(container, "image") or ""
        if img:
            images.append(img)

        ports = _extract_ports(container)
        all_ports.extend(ports)

        checks = _extract_health_checks(container)
        all_health_checks.extend(checks)

        res = _extract_resources(container)
        # Use first non-None values
        cpu_req = cpu_req or res.get("cpu_request")
        cpu_lim = cpu_lim or res.get("cpu_limit")
        mem_req = mem_req or res.get("memory_request")
        mem_lim = mem_lim or res.get("memory_limit")

    return {
        "name": name,
        "namespace": namespace,
        "kind": kind,
        "replicas": replicas,
        "images": list(set(images)),
        "labels": dict(labels),
        "annotations": dict(annotations),
        "cpu_request": cpu_req,
        "cpu_limit": cpu_lim,
        "memory_request": mem_req,
        "memory_limit": mem_lim,
        "ports": list(set(all_ports)),
        "env_vars": all_env_vars,
        "health_checks": all_health_checks,
    }
