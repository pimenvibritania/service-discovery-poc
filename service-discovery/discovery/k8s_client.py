"""Kubernetes API client wrapper with context switching, pagination, and retry logic."""

import json
import time
from pathlib import Path
from typing import Optional

from rich.console import Console

console = Console()


class K8sClient:
    """
    Wraps the kubernetes Python client.
    Supports context switching (staging/production), pagination, retry, and dry-run fixture mode.
    """

    def __init__(self, context_name: str, dry_run: bool = False, fixtures_dir: Optional[str] = None):
        self.context_name = context_name
        self.dry_run = dry_run
        self.fixtures_dir = Path(fixtures_dir) if fixtures_dir else Path(__file__).parent.parent / "fixtures"
        self._apps_v1 = None
        self._core_v1 = None

        if not dry_run:
            self._init_live_client()

    def _init_live_client(self):
        try:
            from kubernetes import client, config
            config.load_kube_config(context=self.context_name)
            self._apps_v1 = client.AppsV1Api()
            self._core_v1 = client.CoreV1Api()
            console.print(f"[green]✓[/green] Connected to context: [bold]{self.context_name}[/bold]")
        except Exception as e:
            console.print(f"[red]✗ Failed to connect to context {self.context_name}: {e}[/red]")
            raise

    def _retry(self, fn, max_attempts=3, backoff=2.0):
        """Retry with exponential backoff."""
        for attempt in range(max_attempts):
            try:
                return fn()
            except Exception as e:
                if attempt == max_attempts - 1:
                    raise
                wait = backoff ** attempt
                console.print(f"[yellow]  Retry {attempt+1}/{max_attempts} after {wait:.1f}s: {e}[/yellow]")
                time.sleep(wait)

    def _load_fixture(self, resource_type: str, namespace: Optional[str] = None) -> list[dict]:
        """Load fixture data from JSON files for dry-run mode."""
        if namespace:
            fixture_path = self.fixtures_dir / f"{resource_type}_{namespace}.json"
            if not fixture_path.exists():
                fixture_path = self.fixtures_dir / f"{resource_type}_all.json"
        else:
            fixture_path = self.fixtures_dir / f"{resource_type}_all.json"

        if not fixture_path.exists():
            return []

        with open(fixture_path) as f:
            data = json.load(f)

        items = data if isinstance(data, list) else data.get("items", [])
        if namespace:
            items = [i for i in items if i.get("metadata", {}).get("namespace") == namespace]
        return items

    def list_namespaces(self) -> list[str]:
        if self.dry_run:
            items = self._load_fixture("namespaces")
            return [i["metadata"]["name"] for i in items]

        def _call():
            result = self._core_v1.list_namespace()
            return [ns.metadata.name for ns in result.items]

        return self._retry(_call)

    def list_deployments(self, namespace: str) -> list:
        if self.dry_run:
            return self._load_fixture("deployments", namespace)

        def _call():
            items = []
            _continue = None
            while True:
                kwargs = {"limit": 100}
                if _continue:
                    kwargs["_continue"] = _continue
                resp = self._apps_v1.list_namespaced_deployment(namespace, **kwargs)
                items.extend(resp.items)
                _continue = resp.metadata._continue
                if not _continue:
                    break
            return items

        return self._retry(_call)

    def list_statefulsets(self, namespace: str) -> list:
        if self.dry_run:
            return self._load_fixture("statefulsets", namespace)

        def _call():
            items = []
            _continue = None
            while True:
                kwargs = {"limit": 100}
                if _continue:
                    kwargs["_continue"] = _continue
                resp = self._apps_v1.list_namespaced_stateful_set(namespace, **kwargs)
                items.extend(resp.items)
                _continue = resp.metadata._continue
                if not _continue:
                    break
            return items

        return self._retry(_call)

    def list_configmaps(self, namespace: str) -> list:
        if self.dry_run:
            return self._load_fixture("configmaps", namespace)

        def _call():
            items = []
            _continue = None
            while True:
                kwargs = {"limit": 100}
                if _continue:
                    kwargs["_continue"] = _continue
                resp = self._core_v1.list_namespaced_config_map(namespace, **kwargs)
                items.extend(resp.items)
                _continue = resp.metadata._continue
                if not _continue:
                    break
            return items

        return self._retry(_call)

    def list_secrets(self, namespace: str) -> list:
        if self.dry_run:
            return self._load_fixture("secrets", namespace)

        def _call():
            items = []
            _continue = None
            while True:
                kwargs = {"limit": 100}
                if _continue:
                    kwargs["_continue"] = _continue
                resp = self._core_v1.list_namespaced_secret(namespace, **kwargs)
                items.extend(resp.items)
                _continue = resp.metadata._continue
                if not _continue:
                    break
            return items

        return self._retry(_call)

    def list_services(self, namespace: str) -> list:
        if self.dry_run:
            return self._load_fixture("services", namespace)

        def _call():
            items = []
            _continue = None
            while True:
                kwargs = {"limit": 100}
                if _continue:
                    kwargs["_continue"] = _continue
                resp = self._core_v1.list_namespaced_service(namespace, **kwargs)
                items.extend(resp.items)
                _continue = resp.metadata._continue
                if not _continue:
                    break
            return items

        return self._retry(_call)
