"""Unit tests for analyzers/env_differ.py"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from models.service_profile import ServiceProfile, DatabaseConnection, ServiceDependency, OperationalInfo
from analyzers.env_differ import diff_environments


def _make_profile(name, ns, env, replicas=2, images=None, databases=None, deps=None, cpu_req="250m", mem_req="512Mi"):
    return ServiceProfile(
        name=name, namespace=ns, environment=env,
        replicas=replicas,
        images=images or [f"registry.cn/mekari/{name}:v1.0.0"],
        databases=databases or [],
        service_dependencies=deps or [],
        cpu_request=cpu_req, cpu_limit="1000m",
        memory_request=mem_req, memory_limit="2Gi",
        operational_info=OperationalInfo(),
    )


def _make_db(db_type, host, env):
    return DatabaseConnection(
        db_type=db_type, host=host, port=5432,
        source_env_key="PG_HOST", source_type="configmap", environment=env
    )


def _make_dep(target, ns):
    return ServiceDependency(
        target_service=target, target_namespace=ns,
        protocol="http", source_env_key="AUTH_HOST",
        discovered_url=f"{target}.{ns}.svc.cluster.local",
    )


class TestMissingService:
    def test_missing_in_production(self):
        stg = [_make_profile("new-service", "nocode", "staging")]
        prd = []
        diffs = diff_environments(stg, prd)
        assert any(d.diff_type == "missing_service" and d.production_value == "MISSING" for d in diffs)

    def test_missing_in_staging(self):
        stg = []
        prd = [_make_profile("legacy-service", "billing", "production")]
        diffs = diff_environments(stg, prd)
        assert any(d.diff_type == "missing_service" and d.staging_value == "MISSING" for d in diffs)

    def test_missing_service_is_critical(self):
        stg = [_make_profile("service-x", "ns", "staging")]
        diffs = diff_environments(stg, [])
        assert diffs[0].severity == "critical"

    def test_no_diff_when_identical(self):
        profile = _make_profile("auth-service", "auth", "staging")
        stg = [_make_profile("auth-service", "auth", "staging")]
        prd = [_make_profile("auth-service", "auth", "production")]
        diffs = diff_environments(stg, prd)
        # No critical diffs expected for identical services
        critical = [d for d in diffs if d.severity == "critical"]
        assert len(critical) == 0


class TestReplicaDiff:
    def test_detects_replica_diff(self):
        stg = [_make_profile("svc", "ns", "staging", replicas=1)]
        prd = [_make_profile("svc", "ns", "production", replicas=3)]
        diffs = diff_environments(stg, prd)
        rd = [d for d in diffs if d.diff_type == "replica_diff"]
        assert len(rd) == 1
        assert rd[0].staging_value == "1"
        assert rd[0].production_value == "3"

    def test_no_replica_diff_when_same(self):
        stg = [_make_profile("svc", "ns", "staging", replicas=2)]
        prd = [_make_profile("svc", "ns", "production", replicas=2)]
        diffs = diff_environments(stg, prd)
        assert not any(d.diff_type == "replica_diff" for d in diffs)


class TestImageDiff:
    def test_detects_image_diff(self):
        stg = [_make_profile("svc", "ns", "staging", images=["registry/svc:v1.0.0"])]
        prd = [_make_profile("svc", "ns", "production", images=["registry/svc:v1.1.0"])]
        diffs = diff_environments(stg, prd)
        assert any(d.diff_type == "image_diff" for d in diffs)

    def test_no_image_diff_when_same(self):
        stg = [_make_profile("svc", "ns", "staging", images=["registry/svc:v1.0.0"])]
        prd = [_make_profile("svc", "ns", "production", images=["registry/svc:v1.0.0"])]
        diffs = diff_environments(stg, prd)
        assert not any(d.diff_type == "image_diff" for d in diffs)


class TestDBDiff:
    def test_detects_different_db_host(self):
        stg = [_make_profile("svc", "ns", "staging", databases=[_make_db("postgresql", "rm-stg.pg.rds.aliyuncs.com", "staging")])]
        prd = [_make_profile("svc", "ns", "production", databases=[_make_db("postgresql", "rm-prd.pg.rds.aliyuncs.com", "production")])]
        diffs = diff_environments(stg, prd)
        db_diffs = [d for d in diffs if d.diff_type == "db_host_diff"]
        assert len(db_diffs) == 2  # one missing in staging, one missing in prod

    def test_detects_db_only_in_staging(self):
        stg = [_make_profile("svc", "ns", "staging", databases=[_make_db("mongodb", "dds-stg.mongodb.rds.aliyuncs.com", "staging")])]
        prd = [_make_profile("svc", "ns", "production")]
        diffs = diff_environments(stg, prd)
        db_diffs = [d for d in diffs if d.diff_type == "db_host_diff"]
        assert len(db_diffs) == 1
        assert db_diffs[0].severity == "warning"


class TestDepDiff:
    def test_detects_dep_only_in_staging(self):
        stg = [_make_profile("svc", "ns", "staging", deps=[_make_dep("auth-service", "auth")])]
        prd = [_make_profile("svc", "ns", "production")]
        diffs = diff_environments(stg, prd)
        dep_diffs = [d for d in diffs if d.diff_type == "dep_diff"]
        assert len(dep_diffs) == 1
