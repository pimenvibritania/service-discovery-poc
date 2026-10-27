"""Unit tests for analyzers/external_dep_detector.py"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from analyzers.external_dep_detector import detect_external_dependencies


def _env(key, value, source_type="configmap"):
    return {"key": key, "value": value, "source_type": source_type,
            "source_name": "cfg", "container_name": "app"}


class TestExternalDependencies:
    def test_detects_alibaba_oss(self):
        env_vars = [_env("OSS_ENDPOINT", "nocode-bucket.oss-ap-southeast-1.aliyuncs.com")]
        result = detect_external_dependencies(env_vars, set())
        assert len(result) == 1
        assert result[0]["category"] == "alibaba_cloud"

    def test_detects_stripe(self):
        env_vars = [_env("STRIPE_API_URL", "https://api.stripe.com")]
        result = detect_external_dependencies(env_vars, set())
        assert len(result) == 1
        assert result[0]["category"] == "stripe"

    def test_detects_sendgrid(self):
        env_vars = [_env("SENDGRID_API_URL", "https://api.sendgrid.com")]
        result = detect_external_dependencies(env_vars, set())
        assert len(result) == 1
        assert result[0]["category"] == "sendgrid"

    def test_detects_firebase(self):
        env_vars = [_env("FIREBASE_URL", "https://mekari-notif.firebaseio.com")]
        result = detect_external_dependencies(env_vars, set())
        assert len(result) == 1
        assert result[0]["category"] == "firebase"

    def test_detects_twilio(self):
        env_vars = [_env("TWILIO_API_URL", "https://api.twilio.com")]
        result = detect_external_dependencies(env_vars, set())
        assert len(result) == 1
        assert result[0]["category"] == "twilio"

    def test_skips_internal_kube_dns(self):
        env_vars = [_env("AUTH_HOST", "auth-service.auth.svc.cluster.local")]
        result = detect_external_dependencies(env_vars, set())
        assert len(result) == 0

    def test_skips_already_classified_keys(self):
        env_vars = [_env("OSS_ENDPOINT", "nocode-bucket.oss-ap-southeast-1.aliyuncs.com")]
        classified = {"OSS_ENDPOINT"}
        result = detect_external_dependencies(env_vars, classified)
        assert len(result) == 0

    def test_skips_secrets(self):
        env_vars = [_env("OSS_ENDPOINT", "nocode-bucket.oss-ap-southeast-1.aliyuncs.com", source_type="secret")]
        result = detect_external_dependencies(env_vars, set())
        assert len(result) == 0

    def test_deduplicates_same_url(self):
        env_vars = [
            _env("STRIPE_URL", "https://api.stripe.com"),
            _env("STRIPE_API_URL", "https://api.stripe.com"),
        ]
        result = detect_external_dependencies(env_vars, set())
        assert len(result) == 1

    def test_friendly_name_generated(self):
        env_vars = [_env("OSS_ENDPOINT", "nocode-bucket.oss-ap-southeast-1.aliyuncs.com")]
        result = detect_external_dependencies(env_vars, set())
        assert "Alibaba" in result[0]["name"]
