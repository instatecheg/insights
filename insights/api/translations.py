import frappe
from frappe.defaults import get_user_default, set_user_default
from frappe.translate import get_all_translations


@frappe.whitelist(allow_guest=True, methods=["GET"])
def get_language():
    language = None
    if frappe.session.user != "Guest":
        language = get_user_default("insights_language") or frappe.db.get_value(
            "User", frappe.session.user, "language"
        )
    if not language:
        language = frappe.db.get_single_value("System Settings", "language")
    return language or "en"


@frappe.whitelist(allow_guest=True, methods=["GET"])
def get_translations():
    return get_all_translations(get_language())


@frappe.whitelist(methods=["POST"])
def set_language(language):
    if frappe.session.user == "Guest":
        frappe.throw("Sign in to change your Insights language.", frappe.PermissionError)
    if language not in ("en", "ar"):
        frappe.throw("Choose English or Arabic.")
    set_user_default("insights_language", language)
    return language
