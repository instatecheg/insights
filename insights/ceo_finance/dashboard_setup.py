import copy
import html

import frappe

from insights.ceo_finance.api import (
    financial_sql,
    link_financial_filters,
    registrations,
    settings,
)


def add_summary_filters(items):
    if any(item.get("layout", {}).get("i") == "ceo-summary-company" for item in items):
        return
    heading = next((item for item in items if "Separate Company Summaries" in item.get("text", "")), None)
    if not heading:
        frappe.throw("The CEO dashboard is missing Separate Company Summaries.")
    y = heading["layout"]["y"] + heading["layout"]["h"]
    for item in items:
        if item["layout"]["y"] >= y:
            item["layout"]["y"] += 3
    for name, x, width, key in (
        ("Company", 0, 6, "ceo-summary-company"),
        ("Reporting Period", 6, 14, "ceo-summary-period"),
    ):
        template = next(item for item in items if item.get("filter_name") == name)
        item = copy.deepcopy(template)
        item["layout"] = {"i": key, "x": x, "y": y, "w": width, "h": 3}
        items.append(item)
    heading["text"] = (
        "<h3>Separate Company Summaries</h3><p>Company and Reporting Period below are "
        "the same dashboard filters as above. They apply to all cards, trends and drill-downs. "
        "Excluded companies show No data.</p>"
    )


def enable_analysis():
    config = settings()
    dashboard = frappe.get_doc("Insights Dashboard v3", config.dashboard)
    items = frappe.parse_json(dashboard.items)
    technical_items, financial_items = [], []
    for item in items:
        if item.get("type") == "chart":
            chart = frappe.get_doc("Insights Chart v3", item["chart"])
            is_technical = chart.query == config.freshness_query or "Latest Journal" in chart.title
        else:
            text = item.get("text", "")
            is_technical = "Live checks read" in text or "Data Freshness" in text or "Synchronization" in text
        (technical_items if is_technical else financial_items).append(copy.deepcopy(item))
    if not config.technical_dashboard:
        technical = frappe.get_doc(
            {
                "doctype": "Insights Dashboard v3",
                "title": "Finance Technical - Sync and Journals",
                "workbook": dashboard.workbook,
                "sync_on_refresh": 0,
                "items": frappe.as_json([]),
            }
        ).insert()
        config.technical_dashboard = technical.name
        config.save()
    else:
        technical = frappe.get_doc("Insights Dashboard v3", config.technical_dashboard)
    if technical_items:
        technical.items = frappe.as_json(
            [
                {
                    "type": "text",
                    "text": "<h2>Finance Technical Checks</h2><p>Import timestamps and live journal checks. Use Refresh on the CEO dashboard to synchronize the financial snapshot; Refresh here reloads these checks.</p>",
                    "layout": {"i": frappe.generate_hash(length=8), "x": 0, "y": 0, "w": 20, "h": 3},
                },
                *technical_items,
            ]
        )
        technical.save()
    for row in registrations():
        if row.status != "Registered":
            continue
        query = frappe.get_doc("Insights Query v3", row.financial_query)
        query.title = f"Financial GL - {row.source_label}"
        query.operations = [
            {"type": "sql", "data_source": row.data_source, "raw_sql": financial_sql(row.source_label)}
        ]
        query.save()
    if not any(item.get("filter_name") == "Reporting Period" for item in financial_items):
        for item in financial_items:
            item["layout"]["y"] += 3
        financial_items[:0] = [
            {
                "type": "filter",
                "filter_name": "Company",
                "filter_type": "String",
                "links": {},
                "layout": {"i": frappe.generate_hash(length=8), "x": 0, "y": 0, "w": 6, "h": 3},
            },
            {
                "type": "filter",
                "filter_name": "Reporting Period",
                "filter_type": "Date",
                "finance_period": True,
                "default_operator": "within",
                "default_value": "Current Year",
                "links": {},
                "layout": {"i": frappe.generate_hash(length=8), "x": 6, "y": 0, "w": 14, "h": 3},
            },
        ]
    for item in financial_items:
        if item.get("type") == "filter":
            item["values_query"] = config.combined_query
        elif item.get("type") == "chart":
            chart = frappe.get_doc("Insights Chart v3", item["chart"])
            chart.title = chart.title.replace("YTD", "Selected Period")
            if chart.chart_type == "Number":
                chart.config = frappe.as_json(
                    {**frappe.parse_json(chart.config), "finance_account_drilldown": True}
                )
            chart.save()
        elif item.get("type") == "text":
            if "CEO Financial Overview" in item.get("text", ""):
                item["text"] = (
                    "<h2>CEO Financial Overview</h2><p>SAR | Select a company and a year/month or custom period. "
                    "Click cards for an account breakdown, then amounts for GL entries and voucher references. "
                    "Refresh synchronizes sources before recalculating. No intercompany eliminations.</p>"
                    '<p><a href="/ceo-company-sources">Company Sources</a> | '
                    f'<a href="/insights/dashboards/{html.escape(technical.name)}">Technical Dashboard - sync and journals</a></p>'
                )
            elif "Posted GL only" in item.get("text", ""):
                item["text"] = (
                    "<p>Posted Income/Expense GL only. Cancelled entries, opening balances and closing transfers are excluded. Reporting period applies to every financial chart. Margin = net profit / revenue; an undefined margin means zero revenue.</p>"
                )
            elif "Company Comparison" in item.get("text", ""):
                item["text"] = (
                    "<h3>Company Comparison</h3><p>The same selected period applies to every company. "
                    "Margin is weighted: net profit / revenue, not an average of monthly percentages.</p>"
                )
    add_summary_filters(financial_items)
    financial_items = [item for item in financial_items if item.get("type") != "financial_report"]
    profit_and_loss = setup_profit_and_loss(config, dashboard)
    for item in financial_items:
        if "CEO Financial Overview" in item.get("text", ""):
            item["text"] += (
                f'<p><a href="/insights/dashboards/{html.escape(profit_and_loss.name)}">'
                "Detailed Profit and Loss - filters, accounts and GL</a></p>"
            )
    link_financial_filters(financial_items)
    dashboard.items = frappe.as_json(financial_items)
    dashboard.save()
    return {
        "dashboard": dashboard.name,
        "technical_dashboard": technical.name,
        "profit_and_loss_dashboard": profit_and_loss.name,
    }


def setup_profit_and_loss(config, ceo):
    if config.profit_and_loss_dashboard:
        dashboard = frappe.get_doc("Insights Dashboard v3", config.profit_and_loss_dashboard)
    else:
        dashboard = frappe.copy_doc(ceo)
        dashboard.title = "Profit and Loss - All Companies"
        dashboard.items = frappe.as_json([])
        dashboard.sync_on_refresh = 0
        dashboard.is_public = 0
        dashboard.share_link = None
        dashboard.insert()
        config.profit_and_loss_dashboard = dashboard.name
        config.save()
    dashboard.items = frappe.as_json(
        [{"type": "financial_report", "layout": {"i": "standalone-pl", "x": 0, "y": 0, "w": 20, "h": 28}}]
    )
    dashboard.sync_on_refresh = 0
    dashboard.save()
    return dashboard
