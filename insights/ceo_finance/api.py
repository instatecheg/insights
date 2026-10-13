import hashlib
import html

import frappe
from frappe.utils import get_datetime, now_datetime

from insights.decorators import has_role
from insights.insights.doctype.insights_data_source_v3.insights_data_source_v3 import db_connections
from insights.insights.doctype.insights_table_v3.insights_table_v3 import get_table_name

TABLE_COLUMNS = {
    "tabGL Entry": {
        "company",
        "account",
        "posting_date",
        "debit",
        "credit",
        "is_cancelled",
        "is_opening",
        "voucher_type",
        "name",
        "voucher_no",
    },
    "tabAccount": {"name", "company", "root_type"},
    "tabCompany": {"name", "default_currency"},
}
ACTIVE = ["Queued", "Importing", "Registered"]


def require_admin():
    if not has_role("Insights Admin"):
        frappe.throw("Company registration requires Insights Admin access.", frappe.PermissionError)


def settings():
    require_admin()
    doc = frappe.get_single("CEO Dashboard Settings")
    if not doc.dashboard or not doc.combined_query or not doc.template_query:
        frappe.throw("Configure CEO Dashboard Settings before registering a company source.")
    frappe.get_doc("Insights Dashboard v3", doc.dashboard).check_permission("write")
    frappe.get_doc("Insights Query v3", doc.combined_query).check_permission("write")
    return doc


def database_identity(source) -> str:
    if source.is_site_db:
        host = frappe.conf.get("db_host") or "localhost"
        port = frappe.conf.get("db_port") or 3306
        database = frappe.conf.db_name
    else:
        host, port, database = source.host, source.port or 3306, source.database_name
    host = str(host or "").strip().lower()
    if host in ("localhost", "127.0.0.1", "::1"):
        host = "localhost"
    return hashlib.sha256(f"{host}:{int(port)}:{database}".encode()).hexdigest()


def source_doc(name):
    source = frappe.get_doc("Insights Data Source v3", name)
    source.check_permission("read")
    if source.status != "Active" or source.database_type != "MariaDB" or not source.is_frappe_db:
        frappe.throw("Select an active MariaDB ERPNext source.")
    if not source.is_site_db and not source.use_ssl:
        frappe.throw("Enable SSL for the remote company database before registering it.")
    return source


def validate_companies(companies):
    if not companies:
        frappe.throw("No ERPNext companies were found in this database.")
    if any(row.get("default_currency") != "SAR" for row in companies):
        frappe.throw("Every company in this source must use SAR. Currency conversion is not configured.")


def inspect_source(name):
    from insights.insights.doctype.insights_team.insights_team import check_table_permission

    source = source_doc(name)
    with db_connections():
        for table, columns in TABLE_COLUMNS.items():
            check_table_permission(name, table, user=frappe.session.user)
            missing = columns - set(source.get_ibis_table(table).columns)
            if missing:
                frappe.throw(f"{table} is missing required columns: {', '.join(sorted(missing))}")
        companies = (
            source.get_ibis_table("tabCompany")
            .select("name", "default_currency")
            .execute()
            .to_dict("records")
        )
    validate_companies(companies)
    return source, companies


def validate_label(label):
    if not isinstance(label, str) or not label.strip() or len(label.strip()) > 60:
        frappe.throw("Enter a display label between 1 and 60 characters.")
    return label.strip()


def sql_literal(value):
    # SQL is parsed by Insights before execution, so use standard SQL quote escaping.
    if "\\" in value or any(ord(char) < 32 for char in value):
        frappe.throw("Display labels cannot contain backslashes or control characters.")
    return "'" + value.replace("'", "''") + "'"


def financial_sql(label):
    label = sql_literal(label)
    return f"""
SELECT {label} AS source_site, c.name AS company, c.default_currency AS currency,
       CONCAT({label}, ' | ', c.name) AS company_label,
       g.name AS gl_entry, g.posting_date AS posting_date,
       CAST(YEAR(g.posting_date) AS CHAR) AS report_year,
       DATE_FORMAT(g.posting_date, '%Y-%m-01') AS month_start,
       g.account AS account, a.root_type AS account_type,
       g.voucher_type AS voucher_type, g.voucher_no AS voucher_no,
       g.debit AS debit, g.credit AS credit,
       CASE WHEN a.root_type='Income' THEN g.credit-g.debit ELSE 0 END AS total_income,
       CASE WHEN a.root_type='Expense' THEN g.debit-g.credit ELSE 0 END AS total_expense,
       g.credit-g.debit AS net_profit
FROM `tabGL Entry` g
JOIN `tabAccount` a ON a.name=g.account AND a.company=g.company
JOIN `tabCompany` c ON c.name=g.company
WHERE g.is_cancelled=0
  AND g.voucher_type <> 'Period Closing Voucher'
  AND COALESCE(g.is_opening,'No') <> 'Yes'
  AND a.root_type IN ('Income','Expense')
  AND g.posting_date <= CURDATE()
"""


def union_operations(records, workbook):
    if not records:
        frappe.throw("At least one registered source is required.")
    return [
        {
            "type": "source" if index == 0 else "union",
            **({"distinct": False} if index else {}),
            "table": {"type": "query", "query_name": row.financial_query, "workbook": workbook},
        }
        for index, row in enumerate(records)
    ]


def registrations():
    return frappe.get_all(
        "CEO Company Source",
        fields=[
            "name",
            "source_label",
            "data_source",
            "status",
            "companies",
            "financial_query",
            "registered_on",
            "message",
            "modified",
        ],
        order_by="creation asc",
    )


def failed_registration_job(record):
    from frappe.utils.background_jobs import get_job
    from rq.job import JobStatus

    if record.status not in ("Queued", "Importing"):
        return False
    job = get_job(f"ceo_register_{record.name}")
    if job:
        return job.get_status() in (JobStatus.FAILED, JobStatus.STOPPED, JobStatus.CANCELED)
    return (now_datetime() - get_datetime(record.modified)).total_seconds() > 300


@frappe.whitelist(methods=["GET"])
def get_state():
    doc = settings()
    sources = frappe.get_list(
        "Insights Data Source v3",
        filters={"status": "Active", "database_type": "MariaDB", "is_frappe_db": 1},
        fields=["name", "title"],
        limit_page_length=0,
    )
    rows = registrations()
    for row in rows:
        if failed_registration_job(row):
            row.status = "Failed"
            row.message = (
                "The background job stopped or was not found. Check the worker and Error Log, then Retry."
            )
    return {"dashboard": doc.dashboard, "sources": sources, "registrations": rows}


@frappe.whitelist(methods=["POST"])
def preview_source(data_source):
    settings()
    source, companies = inspect_source(data_source)
    existing = frappe.db.get_value(
        "CEO Company Source",
        {"database_identity": database_identity(source)},
        ["name", "source_label", "status"],
        as_dict=True,
    )
    return {"companies": companies, "existing": existing}


def save_registration(record):
    for field in ("companies", "dashboard_items"):
        if isinstance(record.get(field), (list, dict)):
            record.set(field, frappe.as_json(record.get(field)))
    record.flags.registration_update = True
    record.save(ignore_permissions=True)


@frappe.whitelist(methods=["POST"])
def register_source(data_source, source_label):
    doc = settings()
    label = validate_label(source_label)
    sql_literal(label)
    source, companies = inspect_source(data_source)
    identity = database_identity(source)
    with frappe.cache.lock(f"{frappe.local.site}:ceo-registration", timeout=60, blocking_timeout=10):
        existing = frappe.db.get_value(
            "CEO Company Source", {"database_identity": identity}, ["name", "status"], as_dict=True
        )
        if existing:
            frappe.throw("This database is already listed. Use Retry if its registration failed.")
        if frappe.db.exists("CEO Company Source", {"source_label": label}):
            frappe.throw("This display label is already used. Choose a unique label.")
        record = frappe.get_doc(
            {
                "doctype": "CEO Company Source",
                "data_source": data_source,
                "source_label": label,
                "database_identity": identity,
                "companies": companies,
                "status": "Queued",
                "message": "Queued: validating and importing the three accounting tables.",
            }
        )
        save_registration(record)
        frappe.enqueue(
            "insights.ceo_finance.api.run_registration",
            registration=record.name,
            job_id=f"ceo_register_{record.name}",
            queue="long",
            timeout=1800,
            enqueue_after_commit=True,
        )
        # Imports commit separately; the queued reservation must outlive this lock.
        frappe.db.commit()
    return {"name": record.name, "dashboard": doc.dashboard}


@frappe.whitelist(methods=["POST"])
def retry_registration(name):
    settings()
    with frappe.cache.lock(f"{frappe.local.site}:ceo-registration", timeout=60, blocking_timeout=10):
        record = frappe.get_doc("CEO Company Source", name)
        record.check_permission("read")
        if record.status != "Failed" and not failed_registration_job(record):
            frappe.throw("Only failed registrations can be retried.")
        from frappe.utils.background_jobs import get_job

        job = get_job(f"ceo_register_{name}")
        if job:
            job.delete()
        record.status = "Queued"
        record.message = "Retry queued."
        save_registration(record)
        frappe.enqueue(
            "insights.ceo_finance.api.run_registration",
            registration=name,
            job_id=f"ceo_register_{name}",
            queue="long",
            timeout=1800,
            enqueue_after_commit=True,
        )
        frappe.db.commit()
    return {"name": name}


def create_company_charts(record, config, query):
    items = frappe.parse_json(frappe.get_doc("Insights Dashboard v3", config.dashboard).items) or []
    templates = []
    for item in items:
        if item.get("type") == "chart":
            chart = frappe.get_doc("Insights Chart v3", item["chart"])
            if chart.query == config.template_query:
                templates.append(chart)
    if not templates:
        frappe.throw("The dashboard has no per-company card templates.")
    y = (
        max(
            (
                item.get("layout", {}).get("y", 0) + item.get("layout", {}).get("h", 0)
                for item in items
                if item.get("type") != "financial_report"
            ),
            default=0,
        )
        + 1
    )
    generated = []
    template_label = frappe.db.get_value(
        "CEO Company Source", {"financial_query": config.template_query}, "source_label"
    )

    def add_item(content, x, row, width, height):
        item = {
            **content,
            "layout": {"i": frappe.generate_hash(length=8), "x": x, "y": row, "w": width, "h": height},
        }
        items.append(item)
        generated.append(item["layout"]["i"])

    add_item({"type": "text", "text": f"<h3>{html.escape(record.source_label)} - SAR</h3>"}, 0, y, 20, 2)
    y += 2
    numbers = [chart for chart in templates if chart.chart_type == "Number"]
    others = [chart for chart in templates if chart.chart_type != "Number"]
    for index, template in enumerate(numbers + others):
        title = company_chart_title(template.title, template_label, record.source_label)
        data_query = frappe.copy_doc(frappe.get_doc("Insights Query v3", template.data_query))
        data_query.title = f"{title} (Data)"
        data_query.operations = replace_query_reference(
            frappe.parse_json(data_query.operations), config.template_query, query.name
        )
        data_query.insert()
        chart = frappe.copy_doc(template)
        chart.title = title
        chart.query, chart.data_query = query.name, data_query.name
        chart.is_public = 0
        chart.insert()
        if index < len(numbers):
            add_item({"type": "chart", "chart": chart.name}, (index % 4) * 5, y + (index // 4) * 4, 5, 4)
        else:
            offset = ((len(numbers) + 3) // 4) * 4 + (index - len(numbers)) * 7
            add_item({"type": "chart", "chart": chart.name}, 0, y + offset, 20, 7)
    record.dashboard_items = generated
    position_detailed_report(items)
    return items


def position_detailed_report(items):
    bottom = max(
        (
            item["layout"]["y"] + item["layout"]["h"]
            for item in items
            if item.get("type") != "financial_report"
        ),
        default=0,
    )
    for item in items:
        if item.get("type") == "financial_report":
            item["layout"]["y"] = bottom + 1


def company_chart_title(title, template_label, label):
    if template_label and title.startswith(template_label + " - "):
        return label + title[len(template_label) :]
    if template_label and f" - {template_label}" in title:
        return title.replace(f" - {template_label}", f" - {label}", 1)
    return f"{label} - {title}"


def replace_query_reference(value, old_name, new_name):
    if isinstance(value, dict):
        return {
            key: new_name
            if key == "query_name" and item == old_name
            else replace_query_reference(item, old_name, new_name)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [replace_query_reference(item, old_name, new_name) for item in value]
    return value


def update_dashboard(config, records, items=None):
    dashboard = frappe.get_doc("Insights Dashboard v3", config.dashboard)
    combined = frappe.get_doc("Insights Query v3", config.combined_query)
    combined.operations = union_operations(records, dashboard.workbook)
    combined.use_live_connection = 0
    combined.save()
    if config.freshness_query:
        query = frappe.get_doc("Insights Query v3", config.freshness_query)
        labels = " ".join(
            f"WHEN {sql_literal(row.data_source)} THEN {sql_literal(row.source_label)}" for row in records
        )
        sources = ", ".join(sql_literal(row.data_source) for row in records)
        query.operations = [
            {
                "type": "sql",
                "data_source": "Site DB",
                "raw_sql": f"""SELECT CASE t.data_source {labels} END AS Source,
 t.`table` AS Accounting_Table,
 DATE_FORMAT(t.last_synced_on,'%Y-%m-%d %H:%i:%s') AS Last_Successful_Sync,
 CASE WHEN t.last_synced_on IS NULL THEN 'NEVER SYNCED'
 WHEN TIMESTAMPDIFF(MINUTE,t.last_synced_on,NOW()) > 90 THEN 'STALE - Import required'
 ELSE 'Recent snapshot - hourly sync' END AS Freshness
 FROM `tabInsights Table v3` t WHERE t.data_source IN ({sources})
 AND t.`table` IN ('tabGL Entry','tabAccount','tabCompany') ORDER BY Source, Accounting_Table""",
            }
        ]
        query.save()
    items = items if items is not None else frappe.parse_json(dashboard.items) or []
    labels = ", ".join(html.escape(row.source_label) for row in records)
    for item in items:
        if item.get("type") == "text" and "CEO Financial Overview" in item.get("text", ""):
            item["text"] = (
                f"<h2>CEO Financial Overview</h2><p>Registered sources: {labels}. "
                "SAR | Select Company and Reporting Period above. Click a card for accounts, "
                "then an account amount for GL entries and voucher references. "
                "Refresh synchronizes registered sources. No intercompany eliminations.</p>"
                '<p><a href="/ceo-company-sources">Company Sources — add a database</a></p>'
                + (
                    f'<p><a href="/insights/dashboards/{html.escape(config.technical_dashboard)}">Technical Dashboard - sync and journals</a></p>'
                    if config.technical_dashboard
                    else ""
                )
                + (
                    f'<p><a href="/insights/dashboards/{html.escape(config.profit_and_loss_dashboard)}">Detailed Profit and Loss - filters, accounts and GL</a></p>'
                    if config.profit_and_loss_dashboard
                    else ""
                )
            )
    link_financial_filters(items)
    dashboard.items = items
    dashboard.sync_on_refresh = 1
    dashboard.save()


def link_financial_filters(items):
    """Apply company/date filters before chart aggregation, including future registrations."""
    charts = [
        frappe.get_doc("Insights Chart v3", item["chart"]) for item in items if item.get("type") == "chart"
    ]
    for item in items:
        if item.get("type") != "filter" or item.get("filter_name") not in ("Company", "Reporting Period"):
            continue
        column = "company_label" if item["filter_name"] == "Company" else "posting_date"
        item["links"] = {chart.name: f"`{chart.query}`.`{column}`" for chart in charts}


def run_registration(registration):
    with frappe.cache.lock(
        f"{frappe.local.site}:ceo-source-registration:{registration}", timeout=1900, blocking_timeout=5
    ):
        return _run_registration(registration)


def _run_registration(registration):
    from insights.insights.doctype.insights_data_source_v3.data_warehouse import (
        execute_warehouse_table_import,
    )

    settings()
    record = frappe.get_doc("CEO Company Source", registration)
    if record.status != "Queued":
        return
    try:
        source, companies = inspect_source(record.data_source)
        if database_identity(source) != record.database_identity:
            frappe.throw("The source database changed since registration. Restore its connection and retry.")
        record.status, record.companies = "Importing", companies
        save_registration(record)
        frappe.db.commit()
        for table in TABLE_COLUMNS:
            record.db_set("message", f"Importing {table} ...", commit=True)
            table_doc = frappe.get_doc("Insights Table v3", get_table_name(record.data_source, table))
            if table_doc.before_import_script or table_doc.row_limit:
                frappe.throw(
                    f"Remove row limits and import scripts from {table} before registering financial data."
                )
            table_doc.sync_mode = "Full"
            table_doc.save()
            with frappe.cache.lock(
                f"{frappe.local.site}:dashboard_import:{record.data_source}:{table}",
                timeout=1800,
                blocking_timeout=60,
            ):
                execute_warehouse_table_import(record.data_source, table)
        # Everything that changes dashboard membership commits atomically, after imports succeed.
        with frappe.cache.lock(f"{frappe.local.site}:ceo-registration", timeout=120, blocking_timeout=60):
            frappe.db.rollback()
            config = settings()
            record.reload()
            query = frappe.get_doc(
                {
                    "doctype": "Insights Query v3",
                    "title": f"Financial GL - {record.source_label}",
                    "workbook": frappe.get_doc("Insights Dashboard v3", config.dashboard).workbook,
                    "is_native_query": 1,
                    "use_live_connection": 0,
                    "locked_data_source": record.data_source,
                    "operations": [
                        {
                            "type": "sql",
                            "data_source": record.data_source,
                            "raw_sql": financial_sql(record.source_label),
                        }
                    ],
                }
            ).insert()
            query.execute(force=True, page_size=1000)
            items = create_company_charts(record, config, query)
            record.financial_query = query.name
            record.status = "Registered"
            record.registered_on = now_datetime()
            record.message = "Registered. Reload the dashboard and click Refresh to update all cards."
            save_registration(record)
            rows = [frappe._dict(row) for row in registrations() if row.status == "Registered"]
            update_dashboard(config, rows, items)
            frappe.db.commit()
    except Exception:
        frappe.db.rollback()
        frappe.log_error(title="CEO company registration failed", message=frappe.get_traceback())
        record = frappe.get_doc("CEO Company Source", registration)
        record.status = "Failed"
        record.message = "Registration failed. Dashboard totals were not changed. Check Error Log and Data Store import logs, correct the issue, then Retry."
        save_registration(record)
        frappe.db.commit()
        raise


def protect_managed_query(doc, method=None):
    if not frappe.db.exists("DocType", "CEO Company Source"):
        return
    record = frappe.db.get_value(
        "CEO Company Source",
        {"financial_query": doc.name, "status": "Registered"},
        ["data_source", "source_label"],
        as_dict=True,
    )
    if record:
        expected = [
            {"type": "sql", "data_source": record.data_source, "raw_sql": financial_sql(record.source_label)}
        ]
        if (
            frappe.parse_json(doc.operations) != expected
            or doc.use_live_connection
            or doc.locked_data_source != record.data_source
            or not doc.is_native_query
        ):
            frappe.throw(
                "This financial query is managed by CEO Company Sources. Its accounting rules and source cannot be edited."
            )
    config = frappe.get_single("CEO Dashboard Settings")
    if config.combined_query == doc.name:
        rows = [frappe._dict(row) for row in registrations() if row.status == "Registered"]
        if rows and (
            frappe.parse_json(doc.operations) != union_operations(rows, doc.workbook)
            or doc.use_live_connection
        ):
            frappe.throw(
                "The combined query is managed by CEO Company Sources. Register databases from the settings screen."
            )


def protect_registered_database(doc, method=None):
    if not frappe.db.exists("DocType", "CEO Company Source"):
        return
    registration = frappe.db.get_value(
        "CEO Company Source",
        {"data_source": doc.name, "status": ["in", ACTIVE]},
        ["name", "database_identity", "status"],
        as_dict=True,
    )
    identity = database_identity(doc)
    if registration and identity != registration.database_identity:
        if doc.name == "Site DB" and doc.is_site_db and frappe.flags.in_migrate:
            if registration.status in ("Queued", "Importing"):
                frappe.throw("Finish pending Site DB registration before migrating to another database.")
            frappe.db.set_value(
                "CEO Company Source",
                registration.name,
                "database_identity",
                identity,
            )
            frappe.logger("insights").info(
                "Rebound native Site DB registrations to the current site database during fixture import."
            )
            return
        frappe.throw(
            "This database is registered on the CEO dashboard. Its host, port and database cannot be changed."
        )


def ensure_hourly_sync():
    if not frappe.db.exists("DocType", "CEO Company Source") or not frappe.db.exists(
        "CEO Company Source", {"status": "Registered"}
    ):
        return
    if not frappe.db.exists("DocType", "Scheduled Job Type"):
        return
    for name in frappe.get_all(
        "Scheduled Job Type", filters={"method": "insights.api.data_store.sync_tables"}, pluck="name"
    ):
        frappe.db.set_value("Scheduled Job Type", name, {"frequency": "Hourly", "stopped": 0})


def adopt_existing_sources():
    """One-time setup: adopt the configured dashboard, without duplicating its cards."""
    config = settings()
    if frappe.db.exists("CEO Company Source"):
        frappe.throw("Existing sources have already been adopted.")
    combined = frappe.get_doc("Insights Query v3", config.combined_query)
    rows = []
    for op in frappe.parse_json(combined.operations):
        if op["type"] not in ("source", "union") or op.get("distinct"):
            frappe.throw("Only a simple UNION ALL dashboard can be adopted.")
        query = frappe.get_doc("Insights Query v3", op["table"]["query_name"])
        operations = frappe.parse_json(query.operations)
        if not query.is_native_query or len(operations) != 1:
            frappe.throw("Only native company financial queries can be adopted.")
        import sqlglot

        expression = sqlglot.parse_one(operations[0]["raw_sql"], read="mysql")
        label = next(column.this.this for column in expression.expressions if column.alias == "source_site")
        source, companies = inspect_source(operations[0]["data_source"])
        record = frappe.get_doc(
            {
                "doctype": "CEO Company Source",
                "source_label": label,
                "data_source": source.name,
                "database_identity": database_identity(source),
                "companies": companies,
                "status": "Registered",
                "financial_query": query.name,
                "registered_on": now_datetime(),
                "message": "Existing source adopted; original company cards retained.",
            }
        )
        # Preserve output while applying the same managed accounting rules to every source.
        query.operations = [{"type": "sql", "data_source": source.name, "raw_sql": financial_sql(label)}]
        query.locked_data_source = source.name
        query.use_live_connection = 0
        query.save()
        save_registration(record)
        rows.append(record)
    update_dashboard(config, rows)
    ensure_hourly_sync()
