"""Unit tests for analyzers/db_detector.py"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from analyzers.db_detector import detect_databases, detect_caches


def _env(key, value, source_type="configmap", source_name="test-config", container="app"):
    return {"key": key, "value": value, "source_type": source_type, "source_name": source_name, "container_name": container}


# ─────────────────────────────────────────────────────────────────────────────
# PostgreSQL detection
# ─────────────────────────────────────────────────────────────────────────────

class TestPostgreSQLDetection:
    def test_detects_pg_host(self):
        env_vars = [_env("PG_HOST", "rm-abcde.pg.rds.aliyuncs.com")]
        classified = set()
        result = detect_databases(env_vars, "staging", classified)
        assert len(result) == 1
        assert result[0]["db_type"] == "postgresql"
        assert result[0]["host"] == "rm-abcde.pg.rds.aliyuncs.com"
        assert result[0]["port"] == 5432
        assert "PG_HOST" in classified

    def test_detects_postgres_host(self):
        env_vars = [_env("POSTGRES_HOST", "rm-xyz.pg.rds.aliyuncs.com")]
        result = detect_databases(env_vars, "production", set())
        assert len(result) == 1
        assert result[0]["db_type"] == "postgresql"

    def test_extracts_alibaba_instance_id(self):
        env_vars = [_env("PG_HOST", "rm-abcde12345.pg.rds.aliyuncs.com")]
        result = detect_databases(env_vars, "staging", set())
        assert result[0]["instance_id"] == "rm-abcde12345"

    def test_extracts_pg_user_companion(self):
        env_vars = [
            _env("PG_HOST", "rm-abcde.pg.rds.aliyuncs.com"),
            _env("PG_USER", "nocode_user"),
            _env("PG_DATABASE", "nocode_db"),
            _env("PG_PORT", "5433"),
        ]
        result = detect_databases(env_vars, "staging", set())
        assert result[0]["user"] == "nocode_user"
        assert result[0]["database_name"] == "nocode_db"
        assert result[0]["port"] == 5433

    def test_detects_from_postgres_url(self):
        env_vars = [_env("DATABASE_URL", "postgresql://user:pass@rm-xyz.pg.rds.aliyuncs.com:5432/mydb")]
        result = detect_databases(env_vars, "staging", set())
        assert len(result) == 1
        db = result[0]
        assert db["db_type"] == "postgresql"
        assert db["host"] == "rm-xyz.pg.rds.aliyuncs.com"
        assert db["port"] == 5432
        assert db["user"] == "user"
        assert db["database_name"] == "mydb"

    def test_deduplicates_same_host(self):
        env_vars = [
            _env("PG_HOST", "rm-abc.pg.rds.aliyuncs.com"),
            _env("POSTGRES_HOST", "rm-abc.pg.rds.aliyuncs.com"),
        ]
        result = detect_databases(env_vars, "staging", set())
        assert len(result) == 1

    def test_source_type_preserved(self):
        env_vars = [_env("PG_HOST", "rm-abc.pg.rds.aliyuncs.com", source_type="direct", source_name=None)]
        result = detect_databases(env_vars, "staging", set())
        assert result[0]["source_type"] == "direct"


# ─────────────────────────────────────────────────────────────────────────────
# MongoDB detection
# ─────────────────────────────────────────────────────────────────────────────

class TestMongoDBDetection:
    def test_detects_mongo_host(self):
        env_vars = [_env("MONGO_HOST", "dds-xyz9876.mongodb.rds.aliyuncs.com")]
        result = detect_databases(env_vars, "staging", set())
        assert len(result) == 1
        assert result[0]["db_type"] == "mongodb"
        assert result[0]["port"] == 27017

    def test_detects_mongodb_uri(self):
        env_vars = [_env("MONGODB_URI", "mongodb://user:pass@dds-abc.mongodb.rds.aliyuncs.com:27017/mydb")]
        result = detect_databases(env_vars, "staging", set())
        assert len(result) == 1
        assert result[0]["db_type"] == "mongodb"
        assert result[0]["host"] == "dds-abc.mongodb.rds.aliyuncs.com"

    def test_extracts_alibaba_dds_instance_id(self):
        env_vars = [_env("MONGO_HOST", "dds-myinstance.mongodb.rds.aliyuncs.com")]
        result = detect_databases(env_vars, "staging", set())
        assert result[0]["instance_id"] == "dds-myinstance"


# ─────────────────────────────────────────────────────────────────────────────
# Redis detection
# ─────────────────────────────────────────────────────────────────────────────

class TestRedisDetection:
    def test_detects_redis_host(self):
        env_vars = [_env("REDIS_HOST", "r-redis001.redis.rds.aliyuncs.com")]
        classified = set()
        result = detect_caches(env_vars, "staging", classified)
        assert len(result) == 1
        assert result[0]["cache_type"] == "redis"
        assert result[0]["port"] == 6379

    def test_detects_redis_url(self):
        env_vars = [_env("REDIS_URL", "redis://:pass@r-abc.redis.rds.aliyuncs.com:6379")]
        result = detect_caches(env_vars, "staging", set())
        assert len(result) == 1
        assert result[0]["host"] == "r-abc.redis.rds.aliyuncs.com"

    def test_detects_cache_host_pattern(self):
        env_vars = [_env("CACHE_HOST", "r-myredis.redis.rds.aliyuncs.com")]
        result = detect_caches(env_vars, "staging", set())
        assert len(result) == 1

    def test_no_false_positive_pg_as_redis(self):
        env_vars = [_env("PG_HOST", "rm-abc.pg.rds.aliyuncs.com")]
        result = detect_caches(env_vars, "staging", set())
        assert len(result) == 0


# ─────────────────────────────────────────────────────────────────────────────
# Elasticsearch detection
# ─────────────────────────────────────────────────────────────────────────────

class TestElasticsearchDetection:
    def test_detects_es_host(self):
        env_vars = [_env("ELASTICSEARCH_HOST", "es-platform.elasticsearch.ap-southeast-1.aliyuncs.com")]
        result = detect_databases(env_vars, "staging", set())
        assert len(result) == 1
        assert result[0]["db_type"] == "elasticsearch"
        assert result[0]["port"] == 9200

    def test_detects_es_url_pattern(self):
        env_vars = [_env("ES_URL", "http://es-platform.elasticsearch.ap-southeast-1.aliyuncs.com:9200")]
        result = detect_databases(env_vars, "staging", set())
        assert len(result) == 1
        assert result[0]["db_type"] == "elasticsearch"


# ─────────────────────────────────────────────────────────────────────────────
# No false positives
# ─────────────────────────────────────────────────────────────────────────────

class TestNoFalsePositives:
    def test_ignores_log_level(self):
        env_vars = [_env("LOG_LEVEL", "info")]
        result = detect_databases(env_vars, "staging", set())
        assert len(result) == 0

    def test_ignores_app_port(self):
        env_vars = [_env("APP_PORT", "8080")]
        result = detect_databases(env_vars, "staging", set())
        assert len(result) == 0

    def test_ignores_secret_env_vars(self):
        env_vars = [_env("PG_HOST", None, source_type="secret")]
        result = detect_databases(env_vars, "staging", set())
        assert len(result) == 0
