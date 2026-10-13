import frappe
from rq.job import JobStatus

from insights.decorators import insights_whitelist


def _dashboard(dashboard_name: str):
    from insights.decorators import has_role

    if not has_role("Insights Admin"):
        frappe.throw(
            frappe._("Source synchronization requires Insights Admin access"), frappe.PermissionError
        )
    frappe.has_permission("Insights Dashboard v3", ptype="read", doc=dashboard_name, throw=True)
    dashboard = frappe.get_doc("Insights Dashboard v3", dashboard_name)
    if not dashboard.sync_on_refresh:
        frappe.throw(frappe._("Source synchronization is not enabled for this dashboard"))
    return dashboard


def _source_tables(dashboard) -> list[tuple[str, str]]:
    from insights.insights.doctype.insights_team.insights_team import check_table_permission

    tables = set()
    for item in frappe.parse_json(dashboard.items) or []:
        if item.get("type") != "chart":
            continue
        chart = frappe.get_doc("Insights Chart v3", item["chart"])
        chart.check_permission("read")
        query = frappe.get_doc("Insights Query v3", chart.data_query)
        query.check_permission("read")
        if query.use_live_connection:
            continue
        references = query.get_source_tables()
        if not references:
            if frappe.db.exists("DocType", "CEO Company Source"):
                config = frappe.get_single("CEO Dashboard Settings")
                if (
                    dashboard.name == config.dashboard
                    and chart.query == config.combined_query
                    and frappe.db.exists("CEO Company Source", {"status": "Archived"})
                    and not frappe.db.exists("CEO Company Source", {"status": "Registered"})
                ):
                    continue
            frappe.throw(frappe._("Cannot determine source tables for chart {0}").format(chart.title))
        for reference in references:
            source, table = reference["data_source"], reference["table_name"]
            check_table_permission(source, table, user=frappe.session.user)
            if frappe.db.get_value("Insights Data Source v3", source, "type") == "REST API":
                frappe.throw(frappe._("REST API sources must be synchronized through their import jobs"))
            tables.add((source, table))
    return sorted(tables)


@insights_whitelist(role="Insights Admin")
def start_dashboard_sync(dashboard_name: str):
    from frappe.utils.background_jobs import get_job

    dashboard = _dashboard(dashboard_name)
    job_id = f"insights_dashboard_sync_{dashboard_name}"
    with (
        frappe.cache.lock(f"{frappe.local.site}:ceo-registration", timeout=120, blocking_timeout=10),
        frappe.cache.lock(f"{frappe.local.site}:{job_id}", timeout=10, blocking_timeout=5),
    ):
        _source_tables(dashboard)
        job = get_job(job_id)
        if not job or job.get_status() not in (
            JobStatus.QUEUED,
            JobStatus.STARTED,
            JobStatus.DEFERRED,
            JobStatus.SCHEDULED,
        ):
            if job:
                job.delete()
            frappe.enqueue(
                run_dashboard_sync,
                dashboard_name=dashboard_name,
                queue="long",
                timeout=1800,
                job_id=job_id,
                at_front=True,
            )
    return {"job_id": job_id}


@insights_whitelist(role="Insights Admin")
def get_dashboard_sync_status(dashboard_name: str, job_id: str):
    from frappe.utils.background_jobs import get_job

    _dashboard(dashboard_name)
    if job_id != f"insights_dashboard_sync_{dashboard_name}":
        frappe.throw(frappe._("Invalid dashboard synchronization job"))
    job = get_job(job_id)
    if not job:
        frappe.throw(frappe._("Synchronization job expired or was not found. Please refresh again."))
    if job.kwargs.get("kwargs", {}).get("dashboard_name") != dashboard_name:
        frappe.throw(frappe._("Invalid dashboard synchronization job"))
    status = job.get_status()
    if status in (JobStatus.FAILED, JobStatus.STOPPED, JobStatus.CANCELED):
        frappe.throw(
            frappe._(
                "Source synchronization failed. Charts have not been refreshed. "
                "Check the Error Log and Data Store import logs, then try again."
            )
        )
    return {"status": status.value}


def run_dashboard_sync(dashboard_name: str):
    from insights.insights.doctype.insights_data_source_v3.data_warehouse import (
        execute_warehouse_table_import,
    )

    tables = _source_tables(_dashboard(dashboard_name))
    for source, table in tables:
        try:
            with frappe.cache.lock(
                f"{frappe.local.site}:dashboard_import:{source}:{table}",
                timeout=1800,
                blocking_timeout=60,
            ):
                execute_warehouse_table_import(source, table)
        except Exception:
            frappe.log_error(
                title="Dashboard source synchronization failed",
                message=f"Dashboard: {dashboard_name}\nSource: {source}\nTable: {table}\n{frappe.get_traceback()}",
            )
            raise
    return {"tables_synced": len(tables)}
