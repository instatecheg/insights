"""Settings validation for the integrated Insights finance module."""

import frappe
from frappe.model.document import Document


class CEODashboardSettings(Document):
    def validate(self):
        if frappe.db.exists("CEO Company Source", {"status": ["in", ["Queued", "Importing", "Registered"]]}):
            before = self.get_doc_before_save()
            if before and any(
                self.get(field) != before.get(field)
                for field in ("dashboard", "combined_query", "template_query", "freshness_query")
            ):
                frappe.throw("Dashboard settings cannot change while company sources are registered.")
        dashboard = frappe.get_doc("Insights Dashboard v3", self.dashboard)
        dashboard.check_permission("write")
        for name in filter(None, (self.combined_query, self.template_query, self.freshness_query)):
            query = frappe.get_doc("Insights Query v3", name)
            query.check_permission("write")
            if query.workbook != dashboard.workbook:
                frappe.throw("All configured queries must belong to the dashboard workbook.")
