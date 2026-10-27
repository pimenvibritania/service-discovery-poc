"""
Service Discovery CLI entry point.
Scans Kubernetes namespaces and exports a comprehensive Excel workbook.
"""

import sys
import os
from datetime import datetime
from pathlib import Path

import click
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn
from rich.table import Table
from rich import print as rprint

# Allow imports from project root
sys.path.insert(0, str(Path(__file__).parent))

import config as cfg
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

console = Console()


# ─────────────────────────────────────────────────────────────────────────────
# Helper: build ServiceProfile from raw workload + parsed deps
# ─────────────────────────────────────────────────────────────────────────────

def _build_profile(workload: dict, parsed: dict, environment: str, svc_type: str = "ClusterIP") -> ServiceProfile:
    databases = [DatabaseConnection(**d) for d in parsed.get("databases", [])]
    service_deps = [ServiceDependency(**d) for d in parsed.get("service_dependencies", [])]
    external_deps = [ExternalDependency(**d) for d in parsed.get("external_dependencies", [])]
    cache_conns = [CacheConnection(**c) for c in parsed.get("cache_connections", [])]
    health_checks = [HealthCheck(**h) for h in workload.get("health_checks", [])]

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
        databases=databases,
        service_dependencies=service_deps,
        external_dependencies=external_deps,
        cache_connections=cache_conns,
        health_checks=health_checks,
        operational_info=OperationalInfo(),
        raw_env_count=parsed.get("raw_env_count", 0),
        unclassified_env_keys=parsed.get("unclassified_env_keys", []),
        k8s_service_type=svc_type,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Core scan function
# ─────────────────────────────────────────────────────────────────────────────

def scan_environment(
    context: str,
    env_label: str,
    namespaces_filter: list[str],
    exclude: list[str],
    dry_run: bool,
    fixtures_dir: str,
    verbose: bool,
) -> list[ServiceProfile]:
    client = K8sClient(context_name=context, dry_run=dry_run, fixtures_dir=fixtures_dir)

    console.print(f"\n[bold cyan]Scanning environment:[/bold cyan] {env_label} (context: {context})")

    # Discover namespaces
    all_namespaces = client.list_namespaces()
    exclude_set = set(exclude + cfg.EXCLUDE_NAMESPACES)

    if namespaces_filter:
        target_ns = [ns for ns in namespaces_filter if ns not in exclude_set]
    else:
        target_ns = [ns for ns in all_namespaces if ns not in exclude_set]

    console.print(f"  Scanning [bold]{len(target_ns)}[/bold] namespace(s): {', '.join(target_ns)}")

    all_profiles: list[ServiceProfile] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Scanning namespaces...", total=len(target_ns))

        for ns in target_ns:
            progress.update(task, description=f"[cyan]{ns}[/cyan]")

            # Step 1: Scan ConfigMaps (needed to resolve env refs)
            configmap_data = scan_configmaps(client, ns)

            # Step 2: Scan Secrets (metadata/keys only)
            secret_keys = scan_secrets(client, ns)

            # Step 3: Scan K8s Services (for dep validation)
            service_registry = scan_services(client, ns)
            known_services = set(service_registry.keys())

            # Step 4: Scan Deployments + StatefulSets
            workloads = scan_workloads(
                client, ns, configmap_data, secret_keys,
                scan_deployments=cfg.SCAN_DEPLOYMENTS,
                scan_statefulsets=cfg.SCAN_STATEFULSETS,
            )

            if verbose:
                console.print(f"    [{ns}] {len(workloads)} workloads, {len(configmap_data)} configmaps, "
                              f"{len(secret_keys)} secrets, {len(service_registry)} services")

            # Step 5: Parse env vars → detect deps
            for workload in workloads:
                parsed = parse_env_vars(workload, env_label, known_services)
                svc_info = service_registry.get(workload["name"], {})
                svc_type = svc_info.get("type", "ClusterIP")
                profile = _build_profile(workload, parsed, env_label, svc_type)
                all_profiles.append(profile)

                if verbose and parsed.get("unclassified_env_keys"):
                    console.print(
                        f"    [yellow]⚠ {workload['name']}: {len(parsed['unclassified_env_keys'])} "
                        f"unclassified env keys[/yellow]"
                    )

            progress.advance(task)

    return all_profiles


# ─────────────────────────────────────────────────────────────────────────────
# Summary table
# ─────────────────────────────────────────────────────────────────────────────

def _print_summary(profiles: list[ServiceProfile], diffs, output_path: str):
    table = Table(title="Discovery Summary", show_header=True, header_style="bold blue")
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")

    table.add_row("Total services scanned", str(len(profiles)))
    table.add_row("Services with DB connections", str(sum(1 for p in profiles if p.databases)))
    table.add_row("Total DB connections", str(sum(len(p.databases) for p in profiles)))
    table.add_row("Services with cache (Redis etc.)", str(sum(1 for p in profiles if p.cache_connections)))
    table.add_row("Services with inter-service deps", str(sum(1 for p in profiles if p.service_dependencies)))
    table.add_row("Broken dependencies (target missing)", str(sum(
        1 for p in profiles for d in p.service_dependencies if not d.validated
    )))
    table.add_row("Services with external deps", str(sum(1 for p in profiles if p.external_dependencies)))
    table.add_row("Environment diffs detected", str(len(diffs)))
    table.add_row("Output file", output_path)

    console.print("\n")
    console.print(table)


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

@click.command()
@click.option("--context", default=None, help="Kubeconfig context (overrides config)")
@click.option("--env", "env_label", default="staging", type=click.Choice(["staging", "production"]),
              show_default=True, help='Environment label: "staging" or "production"')
@click.option("--staging-context", default=None, help="Kubeconfig context for staging")
@click.option("--production-context", default=None, help="Kubeconfig context for production")
@click.option("--namespaces", default=None, help="Comma-separated namespaces to scan (default: all)")
@click.option("--exclude", default=None, help="Comma-separated namespaces to exclude")
@click.option("--output-dir", default=cfg.OUTPUT_DIR, show_default=True, help="Output directory")
@click.option("--format", "fmt", default="xlsx", type=click.Choice(["xlsx", "json", "both"]),
              show_default=True, help="Output format")
@click.option("--diff", "do_diff", is_flag=True, default=False,
              help="Compare staging vs production (scans both environments)")
@click.option("--dry-run", is_flag=True, default=False,
              help="Load from JSON fixtures instead of a live cluster")
@click.option("--fixtures-dir", default=None, help="Directory containing fixture JSON files")
@click.option("--verbose", is_flag=True, default=False, help="Enable debug logging")
def main(context, env_label, staging_context, production_context, namespaces,
         exclude, output_dir, fmt, do_diff, dry_run, fixtures_dir, verbose):
    """
    Service Discovery — scans Kubernetes and exports an Excel dependency map.

    Examples:

    \b
    # Dry-run with sample fixtures
    python main.py --dry-run --env staging

    \b
    # Scan live staging cluster
    python main.py --context my-staging-context --env staging

    \b
    # Compare staging vs production
    python main.py --diff \\
        --staging-context staging-ctx \\
        --production-context production-ctx
    """
    console.print("[bold green]Service Discovery[/bold green] — Kubernetes dependency mapper\n")

    # Resolve contexts
    stg_ctx = staging_context or cfg.STAGING_CONTEXT
    prd_ctx = production_context or cfg.PRODUCTION_CONTEXT

    if context:
        stg_ctx = prd_ctx = context

    # Resolve fixture dir
    fdir = fixtures_dir or str(Path(__file__).parent / "fixtures")

    # Parse namespace lists
    ns_filter = [n.strip() for n in namespaces.split(",")] if namespaces else []
    ex_list   = [n.strip() for n in exclude.split(",")]   if exclude    else []

    diffs = []

    if do_diff:
        console.print("[bold]Running diff mode — scanning both environments[/bold]")
        staging_profiles = scan_environment(stg_ctx, "staging", ns_filter, ex_list, dry_run, fdir, verbose)
        production_profiles = scan_environment(prd_ctx, "production", ns_filter, ex_list, dry_run, fdir, verbose)
        diffs = diff_environments(staging_profiles, production_profiles)
        all_profiles = staging_profiles + production_profiles
        env_label_out = "diff"
    else:
        ctx = stg_ctx if env_label == "staging" else prd_ctx
        all_profiles = scan_environment(ctx, env_label, ns_filter, ex_list, dry_run, fdir, verbose)
        env_label_out = env_label

    # Export
    output_path = None
    if fmt in ("xlsx", "both"):
        output_path = export_excel(all_profiles, diffs, env_label_out, output_dir)
        console.print(f"\n[green]✓ Excel report saved:[/green] {output_path}")

    if fmt in ("json", "both"):
        import json
        date_str = datetime.now().strftime("%Y-%m-%d")
        json_path = os.path.join(output_dir, f"service_discovery_{env_label_out}_{date_str}.json")
        os.makedirs(output_dir, exist_ok=True)
        with open(json_path, "w") as f:
            json.dump(
                {"profiles": [p.model_dump() for p in all_profiles],
                 "diffs": [d.model_dump() for d in diffs]},
                f, indent=2, default=str
            )
        console.print(f"[green]✓ JSON report saved:[/green] {json_path}")
        if not output_path:
            output_path = json_path

    _print_summary(all_profiles, diffs, output_path or output_dir)


if __name__ == "__main__":
    main()
