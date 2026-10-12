import frappe

from insights.ceo_finance.api import require_admin

no_cache = 1


def get_context(context):
    require_admin()
    context.csrf_token = frappe.sessions.get_csrf_token()
    context.title = "CEO Company Sources"
