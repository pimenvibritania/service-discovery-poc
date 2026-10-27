"""
Excel exporter — generates the 6-sheet .xlsx workbook.
Uses openpyxl with professional formatting per the xlsx skill guidelines.
"""

import os
from datetime import datetime
from typing import Optional

from openpyxl import Workbook
from openpyxl.styles import (
    Font, PatternFill, Alignment, Border, Side, GradientFill
)
from openpyxl.utils import get_column_letter
from openpyxl.formatting.rule import CellIsRule, FormulaRule
from openpyxl.worksheet.filters import AutoFilter

from models.service_profile import ServiceProfile, EnvironmentDiff

# ─────────────────────────────────────────────────────────────────────────────
# Color palette
# ─────────────────────────────────────────────────────────────────────────────
HEADER_BG   = "1F4E79"   # Dark blue
HEADER_FG   = "FFFFFF"
ALT_ROW     = "EBF3FB"   # Light blue alternate
YELLOW_FILL = "FFF2CC"   # Manual-fill cells
RED_FILL    = "FFCCCC"   # Critical / broken
ORANGE_FILL = "FFE0B2"   # Warning
GREEN_FILL  = "D9EAD3"   # OK / validated

DB_COLORS = {
    "postgresql": "DBEAFE",
    "mongodb":    "D1FAE5",
    "redis":      "FEF3C7",
    "mysql":      "EDE9FE",
    "elasticsearch": "FEE2E2",
}

SEVERITY_COLORS = {
    "info":     "FFFFFF",
    "warning":  "FFE0B2",
    "critical": "FFCCCC",
}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _hfont(bold=True, color=HEADER_FG, size=10):
    return Font(name="Arial", bold=bold, color=color, size=size)

def _font(bold=False, color="000000", size=10):
    return Font(name="Arial", bold=bold, color=color, size=size)

def _fill(hex_color: str):
    return PatternFill("solid", fgColor=hex_color)

def _center():
    return Alignment(horizontal="center", vertical="center", wrap_text=True)

def _left():
    return Alignment(horizontal="left", vertical="center", wrap_text=True)

def _thin_border():
    s = Side(border_style="thin", color="CCCCCC")
    return Border(left=s, right=s, top=s, bottom=s)

def _apply_header(ws, headers: list[str], row=1):
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=row, column=col, value=h)
        cell.font     = _hfont()
        cell.fill     = _fill(HEADER_BG)
        cell.alignment = _center()
        cell.border   = _thin_border()

def _auto_width(ws, min_w=10, max_w=50):
    for col in ws.columns:
        max_len = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            try:
                max_len = max(max_len, len(str(cell.value or "")))
            except Exception:
                pass
        ws.column_dimensions[col_letter].width = min(max(max_len + 2, min_w), max_w)

def _freeze_and_filter(ws, freeze="A2"):
    ws.freeze_panes = freeze
    ws.auto_filter.ref = ws.dimensions

def _style_data_row(ws, row: int, n_cols: int, fill_color: Optional[str] = None):
    bg = fill_color or (ALT_ROW if row % 2 == 0 else "FFFFFF")
    for col in range(1, n_cols + 1):
        cell = ws.cell(row=row, column=col)
        cell.font      = _font()
        cell.fill      = _fill(bg)
        cell.alignment = _left()
        cell.border    = _thin_border()

def _add_timestamp_note(ws, env: str):
    ws.sheet_properties.tabColor = "1F4E79"
    ws['A1'].comment = None  # ensure clean
    # Write a small metadata row above headers (not a data row)
    # Actually insert as a cell comment on the header cell
    from openpyxl.comments import Comment
    comment = Comment(
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M UTC')} | Environment: {env}",
        "service-discovery"
    )
    ws['A1'].comment = comment


# ─────────────────────────────────────────────────────────────────────────────
# Sheet 1 — Services Overview
# ─────────────────────────────────────────────────────────────────────────────

def _sheet_services_overview(wb: Workbook, profiles: list[ServiceProfile], env: str):
    ws = wb.create_sheet("Services Overview")
    headers = [
        "Service Name", "Namespace", "Environment", "Replicas", "Image",
        "CPU Request", "CPU Limit", "Memory Request", "Memory Limit",
        "# DBs", "# Service Deps", "# External Deps", "Has Cache",
        "Health Endpoint", "K8s Service Type", "Team Owner", "Notes",
    ]
    _apply_header(ws, headers)
    _add_timestamp_note(ws, env)

    for i, p in enumerate(profiles, 2):
        values = [
            p.name,
            p.namespace,
            p.environment,
            p.replicas,
            ", ".join(p.images) if p.images else "",
            p.cpu_request or "",
            p.cpu_limit or "",
            p.memory_request or "",
            p.memory_limit or "",
            len(p.databases),
            len(p.service_dependencies),
            len(p.external_dependencies),
            "Yes" if p.cache_connections else "No",
            next((h.path for h in p.health_checks if h.check_type == "readiness" and h.path), ""),
            p.k8s_service_type or "ClusterIP",
            p.operational_info.team_owner or "",
            p.operational_info.notes or "",
        ]
        for col, v in enumerate(values, 1):
            ws.cell(row=i, column=col, value=v)
        _style_data_row(ws, i, len(headers))

    # Conditional: yellow if # DBs = 0
    db_col = get_column_letter(10)
    ws.conditional_formatting.add(
        f"{db_col}2:{db_col}{len(profiles)+1}",
        CellIsRule(operator="equal", formula=["0"], fill=_fill(YELLOW_FILL))
    )
    # Yellow background for manual-fill columns (Team Owner, Notes)
    for col in [16, 17]:
        for row in range(2, len(profiles) + 2):
            cell = ws.cell(row=row, column=col)
            if not cell.value:
                cell.fill = _fill(YELLOW_FILL)

    _freeze_and_filter(ws)
    _auto_width(ws)


# ─────────────────────────────────────────────────────────────────────────────
# Sheet 2 — Database Connections
# ─────────────────────────────────────────────────────────────────────────────

def _sheet_databases(wb: Workbook, profiles: list[ServiceProfile], env: str):
    ws = wb.create_sheet("Database Connections")
    headers = [
        "Service Name", "Namespace", "Environment", "DB Type", "Host", "Port",
        "Instance ID", "User", "Database Name", "Source Env Key", "Source Type", "Source Name",
    ]
    _apply_header(ws, headers)
    _add_timestamp_note(ws, env)

    row = 2
    for p in profiles:
        for db in p.databases:
            values = [
                p.name, p.namespace, p.environment,
                db.db_type, db.host, db.port,
                db.instance_id or "", db.user or "", db.database_name or "",
                db.source_env_key, db.source_type, db.source_name or "",
            ]
            color = DB_COLORS.get(db.db_type, "FFFFFF")
            for col, v in enumerate(values, 1):
                cell = ws.cell(row=row, column=col, value=v)
                cell.font      = _font()
                cell.fill      = _fill(color)
                cell.alignment = _left()
                cell.border    = _thin_border()
            row += 1

    if row == 2:
        ws.cell(row=2, column=1, value="No database connections found").font = _font(bold=True)

    _freeze_and_filter(ws)
    _auto_width(ws)


# ─────────────────────────────────────────────────────────────────────────────
# Sheet 3 — Service Dependencies
# ─────────────────────────────────────────────────────────────────────────────

def _sheet_service_deps(wb: Workbook, profiles: list[ServiceProfile], env: str):
    ws = wb.create_sheet("Service Dependencies")
    headers = [
        "Source Service", "Source Namespace", "Target Service", "Target Namespace",
        "Environment", "Protocol", "Source Env Key", "Discovered URL", "Target Exists",
    ]
    _apply_header(ws, headers)
    _add_timestamp_note(ws, env)

    row = 2
    for p in profiles:
        for dep in p.service_dependencies:
            values = [
                p.name, p.namespace,
                dep.target_service, dep.target_namespace,
                p.environment, dep.protocol,
                dep.source_env_key, dep.discovered_url,
                "Yes" if dep.validated else "No",
            ]
            for col, v in enumerate(values, 1):
                ws.cell(row=row, column=col, value=v)
            _style_data_row(ws, row, len(headers))

            # Red if target doesn't exist
            if not dep.validated:
                for col in range(1, len(headers) + 1):
                    ws.cell(row=row, column=col).fill = _fill(RED_FILL)
            row += 1

    if row == 2:
        ws.cell(row=2, column=1, value="No inter-service dependencies found").font = _font(bold=True)

    _freeze_and_filter(ws)
    _auto_width(ws)


# ─────────────────────────────────────────────────────────────────────────────
# Sheet 4 — External Dependencies
# ─────────────────────────────────────────────────────────────────────────────

def _sheet_external_deps(wb: Workbook, profiles: list[ServiceProfile], env: str):
    ws = wb.create_sheet("External Dependencies")
    headers = [
        "Service Name", "Namespace", "Environment",
        "Dependency Name", "URL / Endpoint", "Category", "Source Env Key",
    ]
    _apply_header(ws, headers)
    _add_timestamp_note(ws, env)

    row = 2
    for p in profiles:
        for ext in p.external_dependencies:
            values = [
                p.name, p.namespace, p.environment,
                ext.name, ext.url, ext.category, ext.source_env_key,
            ]
            for col, v in enumerate(values, 1):
                ws.cell(row=row, column=col, value=v)
            _style_data_row(ws, row, len(headers))
            row += 1

    if row == 2:
        ws.cell(row=2, column=1, value="No external dependencies found").font = _font(bold=True)

    _freeze_and_filter(ws)
    _auto_width(ws)


# ─────────────────────────────────────────────────────────────────────────────
# Sheet 5 — Environment Diff
# ─────────────────────────────────────────────────────────────────────────────

def _sheet_env_diff(wb: Workbook, diffs: list[EnvironmentDiff]):
    ws = wb.create_sheet("Environment Diff")
    headers = [
        "Service Name", "Namespace", "Diff Type",
        "Staging Value", "Production Value", "Severity", "Notes",
    ]
    _apply_header(ws, headers)

    for i, d in enumerate(diffs, 2):
        values = [
            d.service_name, d.namespace, d.diff_type,
            d.staging_value or "", d.production_value or "",
            d.severity, d.notes or "",
        ]
        color = SEVERITY_COLORS.get(d.severity, "FFFFFF")
        for col, v in enumerate(values, 1):
            cell = ws.cell(row=i, column=col, value=v)
            cell.font      = _font()
            cell.fill      = _fill(color)
            cell.alignment = _left()
            cell.border    = _thin_border()

    if not diffs:
        ws.cell(row=2, column=1, value="No differences found between environments ✓").font = _font(bold=True, color="007700")

    _freeze_and_filter(ws)
    _auto_width(ws)


# ─────────────────────────────────────────────────────────────────────────────
# Sheet 6 — Operations
# ─────────────────────────────────────────────────────────────────────────────

def _sheet_operations(wb: Workbook, profiles: list[ServiceProfile]):
    ws = wb.create_sheet("Operations")
    headers = [
        "Service Name", "Namespace",
        "Team Owner", "Team Slack Channel", "On-Call Rotation",
        "Runbook URL", "SLA Target", "Alerting Channel", "Repo URL", "Notes",
    ]
    _apply_header(ws, headers)

    for i, p in enumerate(profiles, 2):
        op = p.operational_info
        auto_values = [p.name, p.namespace]
        manual_values = [
            op.team_owner or "", op.team_slack_channel or "",
            op.on_call_rotation or "", op.runbook_url or "",
            op.sla_target or "", op.alerting_channel or "",
            op.repo_url or "", op.notes or "",
        ]
        for col, v in enumerate(auto_values + manual_values, 1):
            cell = ws.cell(row=i, column=col, value=v)
            cell.font      = _font()
            cell.alignment = _left()
            cell.border    = _thin_border()
            # Yellow bg for empty manual columns
            if col > 2 and not v:
                cell.fill = _fill(YELLOW_FILL)
            else:
                cell.fill = _fill(ALT_ROW if i % 2 == 0 else "FFFFFF")

    _freeze_and_filter(ws)
    _auto_width(ws)


# ─────────────────────────────────────────────────────────────────────────────
# Main export function
# ─────────────────────────────────────────────────────────────────────────────

def export_excel(
    profiles: list[ServiceProfile],
    diffs: list[EnvironmentDiff],
    env: str,
    output_dir: str,
    output_filename: Optional[str] = None,
) -> str:
    """
    Build and save the 6-sheet workbook.
    Returns the absolute path to the saved file.
    """
    os.makedirs(output_dir, exist_ok=True)

    if not output_filename:
        date_str = datetime.now().strftime("%Y-%m-%d")
        output_filename = f"service_discovery_{env}_{date_str}.xlsx"

    output_path = os.path.join(output_dir, output_filename)

    wb = Workbook()
    # Remove default sheet
    wb.remove(wb.active)

    _sheet_services_overview(wb, profiles, env)
    _sheet_databases(wb, profiles, env)
    _sheet_service_deps(wb, profiles, env)
    _sheet_external_deps(wb, profiles, env)
    _sheet_env_diff(wb, diffs)
    _sheet_operations(wb, profiles)

    wb.save(output_path)
    return output_path
