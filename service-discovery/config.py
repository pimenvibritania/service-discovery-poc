"""Configuration and constants for the service discovery script."""

import os

# Kubernetes contexts (from kubeconfig) — override via CLI or env vars
STAGING_CONTEXT = os.environ.get("STAGING_CONTEXT", "staging-context-name")
PRODUCTION_CONTEXT = os.environ.get("PRODUCTION_CONTEXT", "production-context-name")

# Namespaces to scan (None = scan all)
TARGET_NAMESPACES = None  # e.g., ["billing", "auth", "nocode", "platform"]

# Namespaces to always skip
EXCLUDE_NAMESPACES = [
    "kube-system",
    "kube-public",
    "kube-node-lease",
    "istio-system",
    "monitoring",
    "logging",
    "cert-manager",
    "ingress-nginx",
    "default",
]

# Resource types to scan
SCAN_DEPLOYMENTS = True
SCAN_STATEFULSETS = True
SCAN_DAEMONSETS = False

# Output
OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "./output")
OUTPUT_FILENAME = "service_discovery_{env}_{date}.xlsx"
