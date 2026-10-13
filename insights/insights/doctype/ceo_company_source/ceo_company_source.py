"""Registered source guard for the integrated Insights finance module."""

import frappe
from frappe.model.document import Document


class CEOCompanySource(Document):
    def validate(self):
        if not self.flags.registration_update:
            frappe.throw("Use CEO Company Sources to register, retry, disconnect or reactivate a source.")

    def on_trash(self):
        frappe.throw("Registered sources cannot be deleted directly.")
