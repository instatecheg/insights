"""Source-qualified, read-only standard P&L analysis for the CEO dashboard."""

import copy
from datetime import date
from decimal import Decimal

import frappe
import ibis
from frappe.utils import getdate

from insights.ceo_finance.api import registrations
from insights.insights.doctype.insights_data_source_v3.insights_data_source_v3 import db_connections
from insights.insights.doctype.insights_team.insights_team import check_table_permission

PERIODICITIES = ("Monthly", "Quarterly", "Half-Yearly", "Yearly")
VIEWS = ("Report", "Growth", "Margin")


def sources(dashboard_name):
    configured = frappe.db.get_single_value("CEO Dashboard Settings", "profit_and_loss_dashboard")
    if dashboard_name != configured or frappe.session.user == "Guest":
        frappe.throw(
            "Detailed P&L is available only on the configured Profit and Loss dashboard.",
            frappe.PermissionError,
        )
    frappe.get_doc("Insights Dashboard v3", configured).check_permission("read")
    result = []
    for record in registrations():
        if record.status != "Registered":
            continue
        source = frappe.get_doc("Insights Data Source v3", record.data_source)
        source.check_permission("read")
        if source.status != "Active":
            frappe.throw(f"Source {record.source_label} is not active.")
        result.append((record, source))
    return result


def table(source, name):
    check_table_permission(source.name, f"tab{name}", user=frappe.session.user)
    return source.get_ibis_table(f"tab{name}")


def records(query):
    return query.execute().to_dict("records")


def option_id(source, value):
    return frappe.as_json([source, value], indent=None)


def selected(values, source):
    parsed = [frappe.parse_json(value) for value in values]
    if any(not isinstance(value, list) or len(value) != 2 for value in parsed):
        frappe.throw("Invalid source-qualified filter.")
    return [value[1] for value in parsed if value[0] == source]


def company_matches(label, state):
    if not state or not state.get("operator"):
        return True
    value = state.get("value")
    values = value if isinstance(value, list) else [value]
    if not values or all(value is None or value == "" for value in values):
        return True
    operator = state["operator"]
    if operator in ("in", "=", "is", "equals"):
        return label in values
    if operator in ("not in", "!=", "is not", "not equals"):
        return label not in values
    frappe.throw("Detailed P&L requires Company 'in' or 'not in' selections.")


def dimensions(source):
    definitions = table(source, "Accounting Dimension")
    return records(
        definitions.filter(definitions.disabled == 0).select("label", "fieldname", "document_type")
    )


@frappe.whitelist()
def get_options(dashboard_name):
    options = {
        "companies": [],
        "fiscal_years": [],
        "finance_books": [],
        "cost_centers": [],
        "projects": [],
        "dimensions": [],
    }
    with db_connections():
        for record, source in sources(dashboard_name):
            company_table = table(source, "Company")
            for company in records(company_table.select("name")):
                label = f"{record.source_label} | {company['name']}"
                options["companies"].append({"value": label, "label": label})
            fy = table(source, "Fiscal Year")
            for row in records(
                fy.filter(fy.disabled == 0).select("name", "year_start_date", "year_end_date")
            ):
                options["fiscal_years"].append(
                    {
                        "value": option_id(source.name, row["name"]),
                        "label": f"{record.source_label} | {row['name']}",
                        "start": str(getdate(row["year_start_date"])),
                        "end": str(getdate(row["year_end_date"])),
                    }
                )
            for doctype, key in (
                ("Finance Book", "finance_books"),
                ("Cost Center", "cost_centers"),
                ("Project", "projects"),
            ):
                data = table(source, doctype)
                columns = ["name", *(["company"] if "company" in data.columns else [])]
                for row in records(data.select(*columns).order_by("name")):
                    options[key].append(
                        {
                            "value": option_id(source.name, row["name"]),
                            "label": f"{record.source_label} | {row['name']}",
                            "company": row.get("company"),
                        }
                    )
            for dimension in dimensions(source):
                data = table(source, dimension["document_type"])
                options["dimensions"].append(
                    {
                        "value": option_id(source.name, dimension["fieldname"]),
                        "label": f"{record.source_label} | {dimension['label']}",
                        "options": [
                            {"value": row["name"], "label": row["name"]}
                            for row in records(data.select("name").order_by("name"))
                        ],
                    }
                )
    return options


def validate_filters(filters):
    filters = frappe._dict(frappe.parse_json(filters) or {})
    if filters.get("periodicity", "Monthly") not in PERIODICITIES:
        frappe.throw("Invalid periodicity.")
    if filters.get("view", "Report") not in VIEWS:
        frappe.throw("Invalid report view.")
    if not filters.get("start") or not filters.get("end"):
        frappe.throw("Start and end dates are required.")
    start, end = getdate(filters.start), getdate(filters.end)
    if start > end:
        frappe.throw("End date cannot be before start date.")
    if (end - start).days > 366 * 10:
        frappe.throw("Choose a reporting range of ten years or less.")
    for key in ("finance_books", "cost_centers", "projects"):
        if not isinstance(filters.get(key, []), list):
            frappe.throw(f"Invalid {key} filter.")
    if not isinstance(filters.get("dimensions", {}), dict) or any(
        not isinstance(value, list) for value in filters.get("dimensions", {}).values()
    ):
        frappe.throw("Invalid accounting dimension filters.")
    filters.start, filters.end = start, end
    return filters


def validate_options(dashboard_name, filters):
    if not any(filters.get(key) for key in ("finance_books", "cost_centers", "projects")) and not any(
        filters.get("dimensions", {}).values()
    ):
        return
    options = get_options(dashboard_name)
    for key in ("finance_books", "cost_centers", "projects"):
        valid = {option["value"] for option in options[key]}
        if set(filters.get(key, [])) - valid:
            frappe.throw(f"A selected {key} option is unavailable. Reload P&L Options.")
    valid_dimensions = {
        dimension["value"]: {option["value"] for option in dimension["options"]}
        for dimension in options["dimensions"]
    }
    for key, values in filters.get("dimensions", {}).items():
        if values and (key not in valid_dimensions or set(values) - valid_dimensions[key]):
            frappe.throw("A selected accounting dimension is unavailable. Reload P&L Options.")


def expand_tree(rows, names, parent_field):
    valid = {row["name"] for row in rows}
    if set(names) - valid:
        frappe.throw("A selected tree filter no longer exists. Reload options.")
    included = set(names)
    while True:
        added = {row["name"] for row in rows if row.get(parent_field) in included}
        if added <= included:
            return sorted(included)
        included |= added


def filtered_gl(record, source, company, filters):
    gl = table(source, "GL Entry")
    query = gl.filter(
        (gl.company == company["name"])
        & (gl.is_cancelled == 0)
        & (gl.voucher_type != "Period Closing Voucher")
        & (gl.is_opening.fill_null("No") != "Yes")
        & (gl.posting_date >= filters.start)
        & (gl.posting_date <= min(filters.end, date.today()))
    )
    books = selected(filters.get("finance_books", []), source.name)
    if filters.get("finance_books") and not books:
        return query.filter(ibis.literal(False))
    default_book = company.get("default_finance_book") or ""
    if filters.get("include_default_book_entries", True):
        if books and default_book and any(book != default_book for book in books):
            frappe.throw(
                f"{record.source_label} | {company['name']}: uncheck Include Default FB Entries to use another finance book."
            )
        books.append(default_book)
    query = query.filter(gl.finance_book.fill_null("").isin([*books, ""]))
    for field, key in (("cost_center", "cost_centers"), ("project", "projects")):
        values = filters.get(key, [])
        if not values:
            continue
        names = selected(values, source.name)
        if field == "cost_center" and names:
            centers = table(source, "Cost Center")
            rows = records(centers.select("name", "parent_cost_center"))
            names = expand_tree(rows, names, "parent_cost_center")
        query = query.filter(gl[field].isin(names))
    active_filters = {key: values for key, values in filters.get("dimensions", {}).items() if values}
    if not active_filters:
        return query
    available_dimensions = {
        option_id(source.name, dimension["fieldname"]): dimension for dimension in dimensions(source)
    }
    matched_dimension = False
    for key, names in active_filters.items():
        if key not in available_dimensions:
            identity = frappe.parse_json(key)
            if not isinstance(identity, list) or len(identity) != 2:
                frappe.throw("Invalid accounting dimension.")
            if identity[0] == source.name:
                frappe.throw("The accounting dimension is unavailable. Reload options.")
            continue
        matched_dimension = True
        definition = available_dimensions[key]
        field = definition["fieldname"]
        if field not in gl.columns:
            frappe.throw(f"{record.source_label}: GL Entry has no {definition['label']} dimension.")
        data = table(source, definition["document_type"])
        parent = f"parent_{frappe.scrub(definition['document_type'])}"
        if parent in data.columns:
            names = expand_tree(records(data.select("name", parent)), names, parent)
        else:
            valid = {row["name"] for row in records(data.select("name"))}
            if set(names) - valid:
                frappe.throw("An accounting dimension value is unavailable. Reload options.")
        query = query.filter(gl[field].isin(names))
    if not matched_dimension:
        return query.filter(ibis.literal(False))
    return query


def periods_for(filters):
    from erpnext.accounts.report.financial_statements import get_period_list

    return get_period_list(
        None,
        None,
        filters.start,
        filters.end,
        "Date Range",
        filters.get("periodicity", "Monthly"),
        ignore_fiscal_year=True,
    )


def account_rows(accounts, daily, periods, accumulated=False, fiscal_years=None):
    """Roll leaf values into their real source account parents, without double counting."""
    amounts = {row["name"]: [Decimal(0) for _ in periods] for row in accounts}
    types = {row["name"]: row["root_type"] for row in accounts}
    for entry in daily:
        if entry["account"] not in amounts:
            continue
        posted = getdate(entry["posting_date"])
        amount = Decimal(str(entry["credit"] or 0)) - Decimal(str(entry["debit"] or 0))
        if types[entry["account"]] == "Expense":
            amount = -amount
        for index, period in enumerate(periods):
            start = getdate(period["from_date"])
            end = getdate(period["to_date"])
            if accumulated:
                start = getdate(periods[0]["from_date"])
                for fy in fiscal_years or []:
                    if getdate(fy["year_start_date"]) <= end <= getdate(fy["year_end_date"]):
                        start = max(start, getdate(fy["year_start_date"]))
                        break
            if start <= posted <= end:
                amounts[entry["account"]][index] += amount
    by_name = {row["name"]: row for row in accounts}

    def depth(row):
        seen = {row["name"]}
        parent = row.get("parent_account")
        level = 0
        while parent in by_name:
            if parent in seen:
                frappe.throw("Account hierarchy contains a cycle.")
            seen.add(parent)
            level += 1
            parent = by_name[parent].get("parent_account")
        return level

    for row in sorted(accounts, key=depth, reverse=True):
        parent = row.get("parent_account")
        if parent in amounts:
            amounts[parent] = [a + b for a, b in zip(amounts[parent], amounts[row["name"]], strict=True)]
    result = []
    for row in sorted(accounts, key=lambda row: (row.get("lft", 0), row["name"])):
        values = amounts[row["name"]]
        result.append(
            {
                "account": row["name"],
                "parent_account": row.get("parent_account") or "",
                "account_name": row.get("account_name") or row["name"],
                "account_type": row["root_type"],
                "is_group": bool(row.get("is_group")),
                "indent": depth(row),
                **{period["key"]: float(values[index]) for index, period in enumerate(periods)},
                "total": float(values[-1] if accumulated else sum(values)),
            }
        )
    return result


def apply_view(rows, periods, view, income):
    from erpnext.accounts.report.financial_statements import compute_growth_view_data

    result = copy.deepcopy(rows)
    if view == "Growth":
        compute_growth_view_data(result, periods)
    elif view == "Margin":
        for row in result:
            for key in [*(period["key"] for period in periods), "total"]:
                base = income.get(key, 0)
                row[key] = round(row[key] / base * 100, 2) if base else (0 if row[key] == 0 else None)
    return result


def company_summary(rows, label, source, company):
    totals = {
        root: sum(row["total"] for row in rows if row["account_type"] == root and row["indent"] == 0)
        for root in ("Income", "Expense")
    }
    profit = totals["Income"] - totals["Expense"]
    return {
        "company_label": label,
        "source": source,
        "company": company,
        "income": totals["Income"],
        "expense": totals["Expense"],
        "profit": profit,
        "margin": profit / totals["Income"] * 100 if totals["Income"] else None,
    }


def visible_accounts(rows, periods):
    lookup = {row["account"]: row for row in rows}
    visible = set()
    for row in rows:
        if not any(row[period["key"]] for period in periods):
            continue
        while row and row["account"] not in visible:
            visible.add(row["account"])
            row = lookup.get(row["parent_account"])
    return visible


def company_period_chart(rows, periods):
    values = {
        root: [
            sum(row[period["key"]] for row in rows if row["account_type"] == root and row["indent"] == 0)
            for period in periods
        ]
        for root in ("Income", "Expense")
    }
    return {
        "income": values["Income"],
        "expense": values["Expense"],
        "profit": [a - b for a, b in zip(values["Income"], values["Expense"], strict=True)],
    }


@frappe.whitelist()
def run(dashboard_name, filters):
    filters = validate_filters(filters)
    validate_options(dashboard_name, filters)
    periods = periods_for(filters)
    results = []
    company_summaries = []
    has_data = False
    totals = {"Income": [0.0] * len(periods), "Expense": [0.0] * len(periods)}
    with db_connections():
        for record, source in sources(dashboard_name):
            company_table = table(source, "Company")
            companies = records(company_table.select("name", "default_currency", "default_finance_book"))
            accounts_table = table(source, "Account")
            fy = table(source, "Fiscal Year")
            fiscal_years = records(fy.filter(fy.disabled == 0).select("year_start_date", "year_end_date"))
            for company in companies:
                label = f"{record.source_label} | {company['name']}"
                if not company_matches(label, filters.get("company")):
                    continue
                if company["default_currency"] != "SAR":
                    frappe.throw(f"{label} does not use SAR.")
                accounts = records(
                    accounts_table.filter(
                        (accounts_table.company == company["name"])
                        & accounts_table.root_type.isin(["Income", "Expense"])
                    ).select("name", "account_name", "root_type", "parent_account", "is_group", "lft")
                )
                gl = filtered_gl(record, source, company, filters)
                daily = records(
                    gl.group_by("account", "posting_date").aggregate(
                        debit=gl.debit.sum(), credit=gl.credit.sum()
                    )
                )
                account_names = {row["name"] for row in accounts}
                has_data = has_data or any(entry["account"] in account_names for entry in daily)
                rows = account_rows(
                    accounts, daily, periods, filters.get("accumulated_values", False), fiscal_years
                )
                company_summaries.append(
                    {
                        **company_summary(rows, label, source.name, company["name"]),
                        "source_label": record.source_label,
                        "chart": company_period_chart(rows, periods),
                    }
                )
                income = {**{period["key"]: 0.0 for period in periods}, "total": 0.0}
                for root in ("Income", "Expense"):
                    root_rows = [row for row in rows if row["account_type"] == root and row["indent"] == 0]
                    for index, period in enumerate(periods):
                        amount = sum(row[period["key"]] for row in root_rows)
                        totals[root][index] += amount
                        if root == "Income":
                            income[period["key"]] = amount
                    if root == "Income":
                        income["total"] = sum(row["total"] for row in root_rows)
                display = apply_view(rows, periods, filters.get("view", "Report"), income)
                visible = visible_accounts(rows, periods)
                for raw, row in zip(rows, display, strict=True):
                    if not filters.get("show_zero_values") and raw["account"] not in visible:
                        continue
                    results.append(
                        {**row, "company_label": label, "source": source.name, "company": company["name"]}
                    )
    summary = {}
    for root, values in totals.items():
        summary[root] = values[-1] if filters.get("accumulated_values") else sum(values)
    summary["Net Profit"] = summary["Income"] - summary["Expense"]
    return {
        "periods": [
            {
                "key": period["key"],
                "label": period["label"],
                "start": str(period["from_date"]),
                "end": str(period["to_date"]),
            }
            for period in periods
        ],
        "rows": results,
        "has_data": has_data,
        "summary": summary,
        "companies": company_summaries,
        "chart": {
            "income": totals["Income"],
            "expense": totals["Expense"],
            "profit": [a - b for a, b in zip(totals["Income"], totals["Expense"], strict=True)],
        },
        "currency": "SAR",
        "updated_at": frappe.utils.now(),
    }


@frappe.whitelist()
def get_entries(dashboard_name, filters, source_name, company_name, account, period_key):
    filters = validate_filters(filters)
    validate_options(dashboard_name, filters)
    period = next((p for p in periods_for(filters) if p["key"] == period_key), None)
    if not period:
        frappe.throw("Invalid report period.")
    with db_connections():
        match = next(((r, s) for r, s in sources(dashboard_name) if s.name == source_name), None)
        if not match:
            frappe.throw("This source is not registered.", frappe.PermissionError)
        record, source = match
        company_table = table(source, "Company")
        companies = records(
            company_table.filter(company_table.name == company_name).select("name", "default_finance_book")
        )
        if not companies or not company_matches(
            f"{record.source_label} | {company_name}", filters.get("company")
        ):
            frappe.throw("The company is outside the selected report.", frappe.PermissionError)
        accounts_table = table(source, "Account")
        accounts = records(
            accounts_table.filter(
                (accounts_table.company == company_name)
                & accounts_table.root_type.isin(["Income", "Expense"])
            ).select("name", "parent_account")
        )
        names = expand_tree(accounts, [account], "parent_account")
        if not filters.get("accumulated_values"):
            filters.start = getdate(period["from_date"])
        else:
            fy = table(source, "Fiscal Year")
            for row in records(fy.filter(fy.disabled == 0).select("year_start_date", "year_end_date")):
                if (
                    getdate(row["year_start_date"])
                    <= getdate(period["to_date"])
                    <= getdate(row["year_end_date"])
                ):
                    filters.start = max(filters.start, getdate(row["year_start_date"]))
                    break
        filters.end = getdate(period["to_date"])
        gl = filtered_gl(record, source, companies[0], filters)
        gl = gl.filter(gl.account.isin(names)).order_by("posting_date", "name")
        rows = records(
            gl.select(
                "name",
                "posting_date",
                "account",
                "voucher_type",
                "voucher_no",
                "debit",
                "credit",
                "cost_center",
                "project",
                "finance_book",
            ).limit(1001)
        )
        for row in rows:
            row["posting_date"] = str(getdate(row["posting_date"]))
        return {"rows": rows[:1000], "truncated": len(rows) > 1000}
