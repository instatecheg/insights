"""Non-destructive ownership transfer from the retired CEO Finance add-on."""

import frappe


def migrate_legacy_app():
    if not frappe.db.exists("DocType", "CEO Company Source"):
        return
    if frappe.db.exists("CEO Company Source", {"status": ["in", ["Queued", "Importing"]]}):
        frappe.throw("Finish or retry pending CEO source registrations before migrating the finance app.")

    for doctype in ("CEO Company Source", "CEO Dashboard Settings"):
        frappe.reload_doc("insights", "doctype", frappe.scrub(doctype), force=True)

    if "ceo_finance" in frappe.get_installed_apps():
        from frappe.installer import remove_from_installed_apps

        remove_from_installed_apps("ceo_finance")

    frappe.clear_cache()
    frappe.db.commit()
