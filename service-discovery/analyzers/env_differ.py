"""
Compare the same service across staging and production environments.
Detects drift in DB connections, service dependencies, replicas, images, and resource limits.
"""

from models.service_profile import ServiceProfile, EnvironmentDiff


def diff_environments(
    staging_profiles: list[ServiceProfile],
    production_profiles: list[ServiceProfile],
) -> list[EnvironmentDiff]:
    """
    Compare staging vs production service profiles.
    Returns a list of EnvironmentDiff objects describing all detected differences.
    """
    diffs: list[EnvironmentDiff] = []

    staging_map = {(p.name, p.namespace): p for p in staging_profiles}
    production_map = {(p.name, p.namespace): p for p in production_profiles}

    all_keys = set(staging_map.keys()) | set(production_map.keys())

    for key in sorted(all_keys):
        name, namespace = key
        stg = staging_map.get(key)
        prd = production_map.get(key)

        # Missing in one environment
        if stg and not prd:
            diffs.append(EnvironmentDiff(
                service_name=name, namespace=namespace,
                diff_type="missing_service",
                staging_value="exists",
                production_value="MISSING",
                severity="critical",
                notes="Service exists in staging but not production",
            ))
            continue

        if prd and not stg:
            diffs.append(EnvironmentDiff(
                service_name=name, namespace=namespace,
                diff_type="missing_service",
                staging_value="MISSING",
                production_value="exists",
                severity="critical",
                notes="Service exists in production but not staging",
            ))
            continue

        # Both exist — compare details
        _diff_replicas(name, namespace, stg, prd, diffs)
        _diff_images(name, namespace, stg, prd, diffs)
        _diff_resources(name, namespace, stg, prd, diffs)
        _diff_databases(name, namespace, stg, prd, diffs)
        _diff_service_deps(name, namespace, stg, prd, diffs)

    return diffs


def _diff_replicas(name, ns, stg, prd, diffs):
    if stg.replicas != prd.replicas:
        diffs.append(EnvironmentDiff(
            service_name=name, namespace=ns,
            diff_type="replica_diff",
            staging_value=str(stg.replicas),
            production_value=str(prd.replicas),
            severity="info",
            notes="Replica count differs between environments",
        ))


def _diff_images(name, ns, stg, prd, diffs):
    stg_images = set(stg.images)
    prd_images = set(prd.images)
    if stg_images != prd_images:
        diffs.append(EnvironmentDiff(
            service_name=name, namespace=ns,
            diff_type="image_diff",
            staging_value=", ".join(sorted(stg_images)) or "none",
            production_value=", ".join(sorted(prd_images)) or "none",
            severity="info",
            notes="Different image versions deployed",
        ))


def _diff_resources(name, ns, stg, prd, diffs):
    for field, label in [
        ("cpu_request", "CPU Request"), ("cpu_limit", "CPU Limit"),
        ("memory_request", "Memory Request"), ("memory_limit", "Memory Limit"),
    ]:
        sv = getattr(stg, field)
        pv = getattr(prd, field)
        if sv != pv and (sv or pv):
            diffs.append(EnvironmentDiff(
                service_name=name, namespace=ns,
                diff_type="resource_diff",
                staging_value=sv or "unset",
                production_value=pv or "unset",
                severity="info",
                notes=f"{label} differs",
            ))


def _diff_databases(name, ns, stg, prd, diffs):
    stg_dbs = {(d.db_type, d.host): d for d in stg.databases}
    prd_dbs = {(d.db_type, d.host): d for d in prd.databases}

    all_db_keys = set(stg_dbs.keys()) | set(prd_dbs.keys())
    for db_key in all_db_keys:
        db_type, host = db_key
        in_stg = db_key in stg_dbs
        in_prd = db_key in prd_dbs

        if in_stg and not in_prd:
            diffs.append(EnvironmentDiff(
                service_name=name, namespace=ns,
                diff_type="db_host_diff",
                staging_value=f"{db_type}://{host}",
                production_value="MISSING",
                severity="warning",
                notes=f"{db_type} connection exists in staging but not production",
            ))
        elif in_prd and not in_stg:
            diffs.append(EnvironmentDiff(
                service_name=name, namespace=ns,
                diff_type="db_host_diff",
                staging_value="MISSING",
                production_value=f"{db_type}://{host}",
                severity="warning",
                notes=f"{db_type} connection exists in production but not staging",
            ))
        else:
            # Both have the same type+host — check user/db name differences
            sd = stg_dbs[db_key]
            pd = prd_dbs[db_key]
            if sd.database_name != pd.database_name and (sd.database_name or pd.database_name):
                diffs.append(EnvironmentDiff(
                    service_name=name, namespace=ns,
                    diff_type="db_host_diff",
                    staging_value=f"{db_type} db={sd.database_name}",
                    production_value=f"{db_type} db={pd.database_name}",
                    severity="warning",
                    notes=f"{db_type} database name differs",
                ))


def _diff_service_deps(name, ns, stg, prd, diffs):
    stg_deps = {(d.target_service, d.target_namespace) for d in stg.service_dependencies}
    prd_deps = {(d.target_service, d.target_namespace) for d in prd.service_dependencies}

    for dep in stg_deps - prd_deps:
        diffs.append(EnvironmentDiff(
            service_name=name, namespace=ns,
            diff_type="dep_diff",
            staging_value=f"{dep[0]}.{dep[1]}",
            production_value="MISSING",
            severity="warning",
            notes="Service dependency exists in staging but not production",
        ))

    for dep in prd_deps - stg_deps:
        diffs.append(EnvironmentDiff(
            service_name=name, namespace=ns,
            diff_type="dep_diff",
            staging_value="MISSING",
            production_value=f"{dep[0]}.{dep[1]}",
            severity="warning",
            notes="Service dependency exists in production but not staging",
        ))
