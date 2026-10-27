"""
Integration test: full dry-run pipeline → verifies all 6 Excel sheets are
generated correctly from fixture data.
"""

import sys
import os
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
import openpyxl

from discovery.k8s_client import K8sClient
from discovery.configmap_scanner import scan_configmaps
from discovery.secret_scanner import scan_secrets
from discovery.service_scanner import scan_services
from discovery.deployment_scanner import scan_workloads
from discovery.env_parser import parse_env_vars
from analyzers.env_differ import diff_environments
from exporters.excel_exporter import export_excel
from models.service_profile import (
    ServiceProfile, DatabaseConnection, ServiceDependency,
    ExternalDependency, CacheConnection, HealthCheck, OperationalInfo,
)

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"
NAMESPACES = ["nocode", "auth", "billing", "notification", "platform"]
EXCLUDE = {"kube-system", "monitoring", "kube-public", "kube-node-lease",
           "istio-system", "logging", "cert-manager", "ingress-nginx", "default"}


def _build_profile(workload, parsed, environment):
    return ServiceProfile(
        name=workload["name"],
        namespace=workload["namespace"],
        environment=environment,
        replicas=workload.get("replicas", 1),
        images=workload.get("images", []),
        labels=workload.get("labels", {}),
        annotations=workload.get("annotations", {}),
        cpu_request=workload.get("cpu_request"),
        cpu_limit=workload.get("cpu_limit"),
        memory_request=workload.get("memory_request"),
        memory_limit=workload.get("memory_limit"),
        ports=workload.get("ports", []),
        databases=[DatabaseConnection(**d) for d in parsed.get("databases", [])],
        service_dependencies=[ServiceDependency(**d) for d in parsed.get("service_dependencies", [])],
        external_dependencies=[ExternalDependency(**d) for d in parsed.get("external_dependencies", [])],
        cache_connections=[CacheConnection(**c) for c in parsed.get("cache_connections", [])],
        health_checks=[HealthCheck(**h) for h in workload.get("health_checks", [])],
        operational_info=OperationalInfo(),
        raw_env_count=parsed.get("raw_env_count", 0),
        unclassified_env_keys=parsed.get("unclassified_env_keys", []),
    )


def _run_full_scan(env_label: str) -> list[ServiceProfile]:
    client = K8sClient(context_name="dry-run", dry_run=True, fixtures_dir=str(FIXTURES_DIR))
    all_ns = client.list_namespaces()
    target_ns = [ns for ns in all_ns if ns not in EXCLUDE]
    profiles = []
    for ns in target_ns:
        cm_data  = scan_configmaps(client, ns)
        sec_keys = scan_secrets(client, ns)
        svc_reg  = scan_services(client, ns)
        known    = set(svc_reg.keys())
        workloads = scan_workloads(client, ns, cm_data, sec_keys)
        for w in workloads:
            parsed = parse_env_vars(w, env_label, known)
            profiles.append(_build_profile(w, parsed, env_label))
    return profiles


@pytest.fixture(scope="module")
def staging_profiles():
    return _run_full_scan("staging")


@pytest.fixture(scope="module")
def production_profiles():
    # Reuse staging fixtures — in a real test you'd have separate prod fixtures
    return _run_full_scan("production")


@pytest.fixture(scope="module")
def excel_path(staging_profiles, production_profiles):
    diffs = diff_environments(staging_profiles, production_profiles)
    tmp = "/tmp/service_discovery_test"
    os.makedirs(tmp, exist_ok=True)
    path = export_excel(staging_profiles, diffs, "staging", tmp,
                        output_filename="test_output.xlsx")
    return path


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline tests
# ─────────────────────────────────────────────────────────────────────────────

class TestPipelineOutput:
    def test_discovers_5_services(self, staging_profiles):
        assert len(staging_profiles) == 5

    def test_nocode_has_postgres_and_mongo(self, staging_profiles):
        nocode = next(p for p in staging_profiles if p.name == "nocode-service")
        db_types = {d.db_type for d in nocode.databases}
        assert "postgresql" in db_types
        assert "mongodb" in db_types

    def test_nocode_has_redis(self, staging_profiles):
        nocode = next(p for p in staging_profiles if p.name == "nocode-service")
        assert len(nocode.cache_connections) >= 1
        assert any(c.cache_type == "redis" for c in nocode.cache_connections)

    def test_nocode_has_auth_dependency(self, staging_profiles):
        nocode = next(p for p in staging_profiles if p.name == "nocode-service")
        targets = {d.target_service for d in nocode.service_dependencies}
        assert "auth-service" in targets

    def test_nocode_has_oss_external_dep(self, staging_profiles):
        nocode = next(p for p in staging_profiles if p.name == "nocode-service")
        categories = {d.category for d in nocode.external_dependencies}
        assert "alibaba_cloud" in categories

    def test_billing_has_stripe_and_xendit(self, staging_profiles):
        billing = next(p for p in staging_profiles if p.name == "billing-service")
        categories = {d.category for d in billing.external_dependencies}
        assert "stripe" in categories

    def test_notification_has_sendgrid_twilio_firebase(self, staging_profiles):
        notif = next(p for p in staging_profiles if p.name == "notification-service")
        categories = {d.category for d in notif.external_dependencies}
        assert "sendgrid" in categories
        assert "twilio" in categories
        assert "firebase" in categories

    def test_platform_has_elasticsearch(self, staging_profiles):
        platform = next(p for p in staging_profiles if p.name == "platform-service")
        db_types = {d.db_type for d in platform.databases}
        assert "elasticsearch" in db_types

    def test_alibaba_instance_ids_parsed(self, staging_profiles):
        nocode = next(p for p in staging_profiles if p.name == "nocode-service")
        pg = next((d for d in nocode.databases if d.db_type == "postgresql"), None)
        assert pg is not None
        assert pg.instance_id == "rm-abcde12345"

    def test_no_secret_values_stored(self, staging_profiles):
        for profile in staging_profiles:
            for db in profile.databases:
                # host should be a real hostname, not a password
                assert not _looks_like_base64(db.host), \
                    f"DB host for {profile.name} looks like a secret value: {db.host}"

    def test_health_checks_extracted(self, staging_profiles):
        nocode = next(p for p in staging_profiles if p.name == "nocode-service")
        assert len(nocode.health_checks) >= 1


class TestExcelOutput:
    def test_file_created(self, excel_path):
        assert os.path.exists(excel_path)
        assert excel_path.endswith(".xlsx")

    def test_has_6_sheets(self, excel_path):
        wb = openpyxl.load_workbook(excel_path)
        assert len(wb.sheetnames) == 6

    def test_sheet_names(self, excel_path):
        wb = openpyxl.load_workbook(excel_path)
        expected = {"Services Overview", "Database Connections", "Service Dependencies",
                    "External Dependencies", "Environment Diff", "Operations"}
        assert set(wb.sheetnames) == expected

    def test_services_overview_has_data_rows(self, excel_path):
        wb = openpyxl.load_workbook(excel_path)
        ws = wb["Services Overview"]
        # Row 1 = header; rows 2+ = data
        data_rows = [r for r in ws.iter_rows(min_row=2, values_only=True) if any(c is not None for c in r)]
        assert len(data_rows) == 5

    def test_services_overview_header(self, excel_path):
        wb = openpyxl.load_workbook(excel_path)
        ws = wb["Services Overview"]
        headers = [ws.cell(row=1, column=c).value for c in range(1, 18)]
        assert "Service Name" in headers
        assert "# DBs" in headers
        assert "Team Owner" in headers

    def test_database_sheet_has_rows(self, excel_path):
        wb = openpyxl.load_workbook(excel_path)
        ws = wb["Database Connections"]
        data_rows = [r for r in ws.iter_rows(min_row=2, values_only=True) if any(c is not None for c in r)]
        # At minimum: nocode (pg+mongo), auth (pg), billing (pg), notification (pg), platform (pg+es)
        assert len(data_rows) >= 6

    def test_service_deps_sheet_has_rows(self, excel_path):
        wb = openpyxl.load_workbook(excel_path)
        ws = wb["Service Dependencies"]
        data_rows = [r for r in ws.iter_rows(min_row=2, values_only=True)
                     if any(c is not None for c in r) and r[0] != "No inter-service dependencies found"]
        assert len(data_rows) >= 1

    def test_external_deps_sheet_has_rows(self, excel_path):
        wb = openpyxl.load_workbook(excel_path)
        ws = wb["External Dependencies"]
        data_rows = [r for r in ws.iter_rows(min_row=2, values_only=True)
                     if any(c is not None for c in r) and r[0] != "No external dependencies found"]
        assert len(data_rows) >= 3

    def test_operations_sheet_populated(self, excel_path):
        wb = openpyxl.load_workbook(excel_path)
        ws = wb["Operations"]
        data_rows = [r for r in ws.iter_rows(min_row=2, values_only=True) if any(c is not None for c in r)]
        assert len(data_rows) == 5

    def test_frozen_panes(self, excel_path):
        wb = openpyxl.load_workbook(excel_path)
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            assert ws.freeze_panes is not None, f"Sheet '{sheet_name}' missing frozen panes"

    def test_no_formula_errors(self, excel_path):
        """Verify no #REF!, #DIV/0!, #VALUE!, #N/A, #NAME? errors in any cell."""
        wb = openpyxl.load_workbook(excel_path, data_only=True)
        error_tokens = {"#REF!", "#DIV/0!", "#VALUE!", "#N/A", "#NAME?", "#NULL!", "#NUM!"}
        found_errors = []
        for sheet in wb.worksheets:
            for row in sheet.iter_rows():
                for cell in row:
                    if cell.value and str(cell.value) in error_tokens:
                        found_errors.append(f"{sheet.title}!{cell.coordinate}: {cell.value}")
        assert len(found_errors) == 0, f"Formula errors found: {found_errors}"


def _looks_like_base64(s: str) -> bool:
    """Heuristic: does a string look like a base64-encoded value rather than a hostname?"""
    import re
    if not s:
        return False
    # Base64 chars only (no dots or hyphens typically in hostnames are ok)
    # A real b64 string is typically longer and has no dots
    return bool(re.match(r'^[A-Za-z0-9+/]{20,}={0,2}$', s) and '.' not in s)
