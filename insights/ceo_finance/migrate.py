"""Non-destructive ownership transfer from the retired CEO Finance add-on."""

import frappe

from insights.ceo_finance.api import database_identity


def rebind_native_site_database():
    """A restored Site DB denotes this site's database, not the backup's host."""
    if not frappe.db.exists("DocType", "CEO Company Source"):
        return
    if not frappe.db.exists("Insights Data Source v3", "Site DB"):
        return
    source = frappe.get_doc("Insights Data Source v3", "Site DB")
    if not source.is_site_db:
        frappe.throw("Site DB must be a native site database before migrating CEO registrations.")
    identity = database_identity(source)
    for row in frappe.get_all(
        "CEO Company Source",
        filters={"data_source": "Site DB"},
        fields=["name", "database_identity", "status"],
    ):
        if row.database_identity == identity:
            continue
        if row.status in ("Queued", "Importing"):
            frappe.throw("Finish pending Site DB registration before migrating to another database.")
        frappe.db.set_value("CEO Company Source", row.name, "database_identity", identity)
        frappe.logger("insights").info(
            "Rebound native Site DB registration %s to the current site database during migration.",
            row.name,
        )


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
