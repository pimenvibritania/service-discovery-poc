"""Unit tests for analyzers/service_dep_detector.py"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from analyzers.service_dep_detector import detect_service_dependencies


def _env(key, value, source_type="configmap"):
    return {"key": key, "value": value, "source_type": source_type,
            "source_name": "test-config", "container_name": "app"}


class TestKubeDNSDetection:
    def test_full_kube_dns(self):
        env_vars = [_env("AUTH_SERVICE_HOST", "auth-service.auth.svc.cluster.local")]
        known = {"auth-service"}
        result = detect_service_dependencies(env_vars, known, set())
        assert len(result) == 1
        assert result[0]["target_service"] == "auth-service"
        assert result[0]["target_namespace"] == "auth"
        assert result[0]["validated"] is True

    def test_short_kube_dns(self):
        env_vars = [_env("BILLING_URL", "http://billing-service.billing.svc.cluster.local:8080")]
        known = {"billing-service"}
        result = detect_service_dependencies(env_vars, known, set())
        assert len(result) == 1
        assert result[0]["target_service"] == "billing-service"
        assert result[0]["target_namespace"] == "billing"

    def test_unvalidated_when_not_in_known(self):
        env_vars = [_env("GHOST_HOST", "ghost-service.ghost.svc.cluster.local")]
        result = detect_service_dependencies(env_vars, set(), set())
        assert len(result) == 1
        assert result[0]["validated"] is False

    def test_http_protocol_inferred(self):
        env_vars = [_env("AUTH_URL", "http://auth-service.auth.svc.cluster.local")]
        result = detect_service_dependencies(env_vars, {"auth-service"}, set())
        assert result[0]["protocol"] == "http"

    def test_grpc_protocol_inferred_from_key(self):
        env_vars = [_env("AUTH_GRPC_HOST", "auth-service.auth.svc.cluster.local")]
        result = detect_service_dependencies(env_vars, {"auth-service"}, set())
        assert result[0]["protocol"] == "grpc"

    def test_deduplicates_same_target(self):
        env_vars = [
            _env("AUTH_HOST", "auth-service.auth.svc.cluster.local"),
            _env("AUTH_URL",  "auth-service.auth.svc.cluster.local"),
        ]
        result = detect_service_dependencies(env_vars, {"auth-service"}, set())
        assert len(result) == 1

    def test_classified_keys_populated(self):
        env_vars = [_env("AUTH_HOST", "auth-service.auth.svc.cluster.local")]
        classified = set()
        detect_service_dependencies(env_vars, {"auth-service"}, classified)
        assert "AUTH_HOST" in classified

    def test_skips_secrets(self):
        env_vars = [_env("AUTH_HOST", "auth-service.auth.svc.cluster.local", source_type="secret")]
        result = detect_service_dependencies(env_vars, {"auth-service"}, set())
        assert len(result) == 0

    def test_does_not_pick_up_db_hosts(self):
        env_vars = [_env("PG_HOST", "rm-abc.pg.rds.aliyuncs.com")]
        result = detect_service_dependencies(env_vars, set(), set())
        # Should not classify a DB host as an inter-service dep
        for dep in result:
            assert "rds.aliyuncs.com" not in dep["discovered_url"]
