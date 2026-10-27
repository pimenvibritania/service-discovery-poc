"""Pydantic data models for service profiles."""

from typing import Optional
from pydantic import BaseModel, Field


class DatabaseConnection(BaseModel):
    """Represents a database connection from a service.

    Fields:
        db_type: Type of database (postgresql, mongodb, redis, mysql, elasticsearch, etc.)
        host: Hostname or IP address of the database server
        port: Port number for database connection
        user: Optional username for authentication
        database_name: Optional name of the specific database
        instance_id: AliCloud instance ID extracted from hostname (e.g., pgm-xxx, rm-xxx, dds-xxx)
        source_env_key: Name of the environment variable this connection was discovered from
        source_type: How the connection was discovered (configmap, secret, direct)
        source_name: Name of the ConfigMap or Secret containing this connection
        environment: Deployment environment (staging, production, etc.)
        container_name: Optional Kubernetes container name where this connection is used
        cloud_resource_ref: Optional reference ID to the corresponding CloudResource for easier lookup
    """
    db_type: str                        # postgresql, mongodb, redis, mysql, elasticsearch
    host: str
    port: int
    user: Optional[str] = None
    database_name: Optional[str] = None
    instance_id: Optional[str] = None  # Cloud provider instance ID (parsed from hostname)
    source_env_key: str
    source_type: str                    # configmap, secret, direct
    source_name: Optional[str] = None
    environment: str
    container_name: Optional[str] = None
    cloud_resource_ref: Optional[str] = None  # Reference to CloudResource.instance_id for this database


class ServiceDependency(BaseModel):
    """Represents a dependency on another Kubernetes service.

    Fields:
        target_service: Name of the target Kubernetes service
        target_namespace: Kubernetes namespace of the target service
        protocol: Protocol used (http, grpc, tcp, etc.)
        source_env_key: Environment variable from which this dependency was discovered
        discovered_url: Full URL or endpoint used to reach the target service
        validated: Whether the target service exists and is accessible in the cluster
        container_name: Optional name of the container that makes this call
    """
    target_service: str
    target_namespace: str
    protocol: str                       # http, grpc, tcp
    source_env_key: str
    discovered_url: str
    validated: bool = False             # Whether target exists as a K8s Service
    container_name: Optional[str] = None


class ExternalDependency(BaseModel):
    """Represents an external dependency on a third-party service or AliCloud resource.

    Fields:
        name: Human-readable name of the external service
        url: URL or endpoint of the external service
        category: Category of service (alibaba_cloud, third_party_api, cdn_storage, webhook, etc.)
        source_env_key: Environment variable from which this dependency was discovered
        container_name: Optional name of the container using this dependency
        instance_id: Optional AliCloud instance/resource ID if this is an AliCloud service
        cloud_resource_type: Optional type of AliCloud resource (rds_postgresql, redis, elasticsearch, etc.)
    """
    name: str
    url: str
    category: str                       # alibaba_cloud, third_party_api, cdn_storage, etc.
    source_env_key: str
    container_name: Optional[str] = None
    instance_id: Optional[str] = None  # AliCloud resource ID
    cloud_resource_type: Optional[str] = None  # Type of AliCloud resource if applicable


class CacheConnection(BaseModel):
    """Represents a cache/in-memory store connection from a service.

    Fields:
        cache_type: Type of cache (redis, memcached, etc.)
        host: Hostname or IP address of the cache server
        port: Port number for cache connection
        instance_id: AliCloud instance ID for the cache (e.g., r-xxx for Redis)
        source_env_key: Name of the environment variable this connection was discovered from
        source_type: How the connection was discovered (configmap, secret, direct)
        source_name: Name of the ConfigMap or Secret containing this connection
        environment: Deployment environment (staging, production, etc.)
        container_name: Optional Kubernetes container name where this connection is used
        cloud_resource_ref: Optional reference ID to the corresponding CloudResource
    """
    cache_type: str                     # redis, memcached
    host: str
    port: int
    instance_id: Optional[str] = None  # AliCloud instance ID (e.g., r-xxx for Redis)
    source_env_key: str
    source_type: str = "direct"
    source_name: Optional[str] = None
    environment: str = ""
    container_name: Optional[str] = None
    cloud_resource_ref: Optional[str] = None  # Reference to CloudResource.instance_id for this cache


class HealthCheck(BaseModel):
    check_type: str                     # liveness, readiness, startup
    path: Optional[str] = None
    port: int
    scheme: str = "HTTP"               # HTTP, TCP, exec


class OperationalInfo(BaseModel):
    """Empty by default — for manual enrichment."""
    team_owner: Optional[str] = None
    team_slack_channel: Optional[str] = None
    on_call_rotation: Optional[str] = None
    runbook_url: Optional[str] = None
    sla_target: Optional[str] = None
    alerting_channel: Optional[str] = None
    repo_url: Optional[str] = None
    notes: Optional[str] = None


class ServiceProfile(BaseModel):
    name: str
    namespace: str
    environment: str
    replicas: int = 0
    images: list[str] = Field(default_factory=list)
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)
    cpu_request: Optional[str] = None
    cpu_limit: Optional[str] = None
    memory_request: Optional[str] = None
    memory_limit: Optional[str] = None
    ports: list[int] = Field(default_factory=list)
    databases: list[DatabaseConnection] = Field(default_factory=list)
    service_dependencies: list[ServiceDependency] = Field(default_factory=list)
    external_dependencies: list[ExternalDependency] = Field(default_factory=list)
    cache_connections: list[CacheConnection] = Field(default_factory=list)
    health_checks: list[HealthCheck] = Field(default_factory=list)
    operational_info: OperationalInfo = Field(default_factory=OperationalInfo)
    raw_env_count: int = 0
    unclassified_env_keys: list[str] = Field(default_factory=list)
    k8s_service_type: Optional[str] = None  # ClusterIP, NodePort, LoadBalancer


class EnvironmentDiff(BaseModel):
    service_name: str
    namespace: str
    diff_type: str      # missing_service, db_host_diff, replica_diff, image_diff, dep_diff
    staging_value: Optional[str] = None
    production_value: Optional[str] = None
    severity: str = "info"   # info, warning, critical
    notes: Optional[str] = None


class CloudResource(BaseModel):
    """Comprehensive model for any AliCloud infrastructure resource.

    This model represents any cloud resource that services depend on, enabling
    reverse-lookup queries for incident response (e.g., "which services use this
    RDS instance?") and comprehensive dependency mapping.

    Fields:
        resource_type: Type of AliCloud resource. Allowed values:
            Databases: rds_postgresql, rds_mysql, rds_mongodb, dynamodb_table
            Cache: redis, memcached
            Search/Analytics: elasticsearch, opensearch, maxcompute
            Storage: oss_bucket, nas_filesystem
            Logging: sls_log_project, sls_log_store
            Messaging: mns_queue, mns_topic, kafka_instance
            Networking: slb_load_balancer, alb_load_balancer, private_zone, cen, vpn
            Compute: function_compute, container_registry
            Communication: direct_mail, sms_service, push_service
            API: api_gateway, other_api_service
            Other: third_party_service, other

        instance_id: AliCloud instance/resource ID. Examples:
            - PostgreSQL: pgm-abcdef1234567890
            - MySQL: rm-abcdef1234567890
            - MongoDB: dds-abcdef1234567890
            - Redis: r-abcdef1234567890
            - Elasticsearch: es-abcdef1234567890
            - OSS Bucket: my-bucket-name
            - SLS Project: my-log-project
            - Kafka: alikafka-cn-xyz12345
            This is the primary lookup key for reverse dependency queries.

        resource_name: Human-readable name or alias for this resource, if available
            from AliCloud tags or configuration

        region: AliCloud region where the resource is deployed. Examples:
            ap-southeast-1, cn-hangzhou, cn-beijing, ap-northeast-1, eu-west-1

        endpoint: Full hostname or endpoint used to connect to this resource. Examples:
            pgm-xxx.pg.rds.aliyuncs.com
            r-xxx.redis.rds.aliyuncs.com
            my-bucket.oss-cn-hangzhou.aliyuncs.com

        vpc_id: VPC ID this resource belongs to, if applicable. Useful for determining
            if a service can reach this resource.

        shared: Boolean indicating whether multiple services share this resource.
            Helps identify shared infrastructure for cost optimization and
            blast radius analysis during incidents.

        services_using: List of service names that connect to or use this resource.
            Populated during analysis and updated as dependencies are discovered.
            Critical for incident response: quickly identify which services are affected
            if this resource goes down.

        source_env_keys: Set of environment variable names across all services that
            reference this cloud resource. Helps correlate configuration changes
            across the entire service mesh.

        tags: Dictionary of AliCloud resource tags for additional metadata and filtering.
            Example: {'env': 'production', 'team': 'data-platform', 'cost-center': '12345'}

        environment: Deployment environment this resource is in (staging, production, etc.)
            Useful for environment-specific incident response and capacity planning.

        category: Logical category for organizing resources by function:
            database, cache, storage, messaging, logging, networking, compute, communication, api, other
            Simpler than resource_type for high-level filtering and queries.

        last_discovered: ISO 8601 timestamp of when this resource was last discovered
            through dependency scanning. Helps identify stale entries.

        notes: Optional free-form notes about this resource, usage patterns, or known issues
    """
    resource_type: str
    instance_id: str  # Primary lookup key
    resource_name: Optional[str] = None
    region: Optional[str] = None
    endpoint: Optional[str] = None
    vpc_id: Optional[str] = None
    shared: bool = False
    services_using: list[str] = Field(default_factory=list)
    source_env_keys: set[str] = Field(default_factory=set)
    tags: dict[str, str] = Field(default_factory=dict)
    environment: Optional[str] = None
    category: str = "other"  # database, cache, storage, messaging, logging, networking, compute, communication, api, other
    last_discovered: Optional[str] = None
    notes: Optional[str] = None


class CloudResourceIndex(BaseModel):
    """Reverse-lookup index for rapid incident response and dependency analysis.

    This model provides instant answers to critical operational questions:
    - "PostgreSQL instance pgm-12345 is down — which services are affected?"
    - "How many services depend on this Redis cluster?"
    - "Which services are accessing the same RDS instance from both staging and production?"

    The index is materialized from CloudResource.services_using and should be
    regenerated after each discovery run to maintain accuracy.

    Fields:
        instance_id: AliCloud resource instance ID (primary lookup key)

        resource_type: Type of resource for contextual filtering in queries
            (e.g., show me all services using RDS instances)

        affected_services: List of service names that depend on this resource,
            with their deployment environments included for context.
            Format: [{"service_name": "payment-api", "namespace": "production"}]

        dependency_count: Number of unique services depending on this resource.
            Useful for sorting and identifying high-impact resources.

        shared_services_count: Number of services that share this single resource
            instance with others. High counts indicate shared infrastructure
            requiring careful change management.

        critical: Boolean flag indicating if this resource is used by critical services.
            Can be derived from service criticality metadata or manually set.

        incident_impact_estimate: Free-form text describing estimated blast radius
            if this resource becomes unavailable. Example: "Payment processing blocked,
            ~5000 transactions/hour affected, estimated revenue impact $2K/hour"
    """
    instance_id: str  # Primary lookup key
    resource_type: str
    affected_services: list[dict[str, str]] = Field(default_factory=list)
    dependency_count: int = 0
    shared_services_count: int = 0
    critical: bool = False
    incident_impact_estimate: Optional[str] = None


class ServiceProfile(BaseModel):
    """Complete profile of a Kubernetes microservice and all its dependencies.

    This is the primary output model containing all discovered information about
    a single service, including cloud resource connections, inter-service dependencies,
    operational metadata, and configuration details.

    Fields:
        name: Kubernetes Deployment/StatefulSet name
        namespace: Kubernetes namespace
        environment: Deployment environment (staging, production, etc.)
        replicas: Current replica count
        images: List of container images used
        labels: Kubernetes labels applied to the Deployment
        annotations: Kubernetes annotations
        cpu_request: CPU request per pod (e.g., "500m")
        cpu_limit: CPU limit per pod
        memory_request: Memory request per pod (e.g., "512Mi")
        memory_limit: Memory limit per pod
        ports: List of container ports exposed
        databases: List of DatabaseConnection objects for this service
        service_dependencies: List of ServiceDependency objects to other K8s services
        external_dependencies: List of ExternalDependency objects to external services
        cache_connections: List of CacheConnection objects for in-memory stores
        health_checks: List of HealthCheck objects (readiness, liveness probes)
        operational_info: OperationalInfo object with team, runbook, and alerting details
        raw_env_count: Total number of environment variables in this service
        unclassified_env_keys: List of env var names that couldn't be categorized
        k8s_service_type: Kubernetes Service type (ClusterIP, NodePort, LoadBalancer, etc.)
    """
    name: str
    namespace: str
    environment: str
    replicas: int = 0
    images: list[str] = Field(default_factory=list)
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)
    cpu_request: Optional[str] = None
    cpu_limit: Optional[str] = None
    memory_request: Optional[str] = None
    memory_limit: Optional[str] = None
    ports: list[int] = Field(default_factory=list)
    databases: list[DatabaseConnection] = Field(default_factory=list)
    service_dependencies: list[ServiceDependency] = Field(default_factory=list)
    external_dependencies: list[ExternalDependency] = Field(default_factory=list)
    cache_connections: list[CacheConnection] = Field(default_factory=list)
    health_checks: list[HealthCheck] = Field(default_factory=list)
    operational_info: OperationalInfo = Field(default_factory=OperationalInfo)
    raw_env_count: int = 0
    unclassified_env_keys: list[str] = Field(default_factory=list)
    k8s_service_type: Optional[str] = None  # ClusterIP, NodePort, LoadBalancer
