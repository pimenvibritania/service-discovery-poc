"""Unit tests for discovery scanners using fixture data."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from discovery.k8s_client import K8sClient
from discovery.configmap_scanner import scan_configmaps
from discovery.secret_scanner import scan_secrets
from discovery.service_scanner import scan_services
from discovery.deployment_scanner import scan_workloads

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def client():
    return K8sClient(context_name="dry-run-test", dry_run=True, fixtures_dir=str(FIXTURES_DIR))


# ─────────────────────────────────────────────────────────────────────────────
# ConfigMap Scanner
# ─────────────────────────────────────────────────────────────────────────────

class TestConfigMapScanner:
    def test_loads_nocode_configmap(self, client):
        data = scan_configmaps(client, "nocode")
        assert "nocode-config" in data
        assert data["nocode-config"]["PG_HOST"] == "rm-abcde12345.pg.rds.aliyuncs.com"

    def test_loads_auth_configmap(self, client):
        data = scan_configmaps(client, "auth")
        assert "auth-config" in data
        assert "PG_HOST" in data["auth-config"]

    def test_returns_empty_for_unknown_namespace(self, client):
        data = scan_configmaps(client, "nonexistent-ns")
        assert data == {}


# ─────────────────────────────────────────────────────────────────────────────
# Secret Scanner (keys only, never values)
# ─────────────────────────────────────────────────────────────────────────────

class TestSecretScanner:
    def test_loads_secret_keys(self, client):
        keys = scan_secrets(client, "nocode")
        assert "nocode-secrets" in keys
        assert "PG_PASSWORD" in keys["nocode-secrets"]

    def test_values_are_never_stored(self, client):
        # The scanner returns {name: [key_list]}, never actual values
        keys = scan_secrets(client, "nocode")
        assert isinstance(keys["nocode-secrets"], list)
        # Values should only be key names (strings), never decoded values
        for k in keys["nocode-secrets"]:
            assert isinstance(k, str)
            assert len(k) < 100  # a key name, not a base64-encoded value

    def test_returns_empty_for_unknown_namespace(self, client):
        keys = scan_secrets(client, "nonexistent-ns")
        assert keys == {}


# ─────────────────────────────────────────────────────────────────────────────
# Service Scanner
# ─────────────────────────────────────────────────────────────────────────────

class TestServiceScanner:
    def test_loads_nocode_service(self, client):
        registry = scan_services(client, "nocode")
        assert "nocode-service" in registry
        svc = registry["nocode-service"]
        assert svc["type"] == "ClusterIP"
        assert svc["ports"][0]["port"] == 8080

    def test_loads_platform_loadbalancer(self, client):
        registry = scan_services(client, "platform")
        assert "platform-service" in registry
        assert registry["platform-service"]["type"] == "LoadBalancer"

    def test_returns_empty_for_unknown_namespace(self, client):
        registry = scan_services(client, "nonexistent-ns")
        assert registry == {}


# ─────────────────────────────────────────────────────────────────────────────
# Deployment Scanner
# ─────────────────────────────────────────────────────────────────────────────

class TestDeploymentScanner:
    def test_scans_nocode_deployment(self, client):
        cm_data = scan_configmaps(client, "nocode")
        sec_keys = scan_secrets(client, "nocode")
        workloads = scan_workloads(client, "nocode", cm_data, sec_keys)
        assert len(workloads) == 1
        w = workloads[0]
        assert w["name"] == "nocode-service"
        assert w["replicas"] == 3
        assert "registry.cn-hangzhou.aliyuncs.com/mekari/nocode-service:v2.1.0" in w["images"]

    def test_resolves_configmap_env_vars(self, client):
        cm_data = scan_configmaps(client, "nocode")
        sec_keys = scan_secrets(client, "nocode")
        workloads = scan_workloads(client, "nocode", cm_data, sec_keys)
        env_vars = workloads[0]["env_vars"]
        pg_host_ev = next((ev for ev in env_vars if ev["key"] == "PG_HOST"), None)
        assert pg_host_ev is not None
        assert pg_host_ev["value"] == "rm-abcde12345.pg.rds.aliyuncs.com"
        assert pg_host_ev["source_type"] == "configmap"

    def test_secret_env_vars_have_no_value(self, client):
        cm_data = scan_configmaps(client, "nocode")
        sec_keys = scan_secrets(client, "nocode")
        workloads = scan_workloads(client, "nocode", cm_data, sec_keys)
        env_vars = workloads[0]["env_vars"]
        secret_evs = [ev for ev in env_vars if ev["source_type"] == "secret"]
        for ev in secret_evs:
            assert ev["value"] is None, f"Secret {ev['key']} should never have a value stored"

    def test_extracts_health_checks(self, client):
        cm_data = scan_configmaps(client, "nocode")
        sec_keys = scan_secrets(client, "nocode")
        workloads = scan_workloads(client, "nocode", cm_data, sec_keys)
        hc = workloads[0]["health_checks"]
        assert len(hc) >= 1
        assert any(h["path"] == "/health/ready" for h in hc)

    def test_extracts_resource_limits(self, client):
        cm_data = scan_configmaps(client, "nocode")
        sec_keys = scan_secrets(client, "nocode")
        workloads = scan_workloads(client, "nocode", cm_data, sec_keys)
        w = workloads[0]
        assert w["cpu_request"] == "250m"
        assert w["cpu_limit"] == "1000m"
        assert w["memory_request"] == "512Mi"
        assert w["memory_limit"] == "2Gi"

    def test_scans_all_namespaces(self, client):
        total = 0
        for ns in ["nocode", "auth", "billing", "notification", "platform"]:
            cm_data = scan_configmaps(client, ns)
            sec_keys = scan_secrets(client, ns)
            workloads = scan_workloads(client, ns, cm_data, sec_keys)
            total += len(workloads)
        assert total == 5
