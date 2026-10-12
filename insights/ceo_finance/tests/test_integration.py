import json
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

import frappe

from insights import hooks
from insights.ceo_finance import api, migrate


class TestFinanceIntegration(TestCase):
    def test_native_identity_is_rebound_before_fixture_validation(self):
        source = frappe._dict(name="Site DB", is_site_db=1)
        row = frappe._dict(name="local-registration", database_identity="old-site", status="Registered")
        with (
            patch.object(frappe.db, "exists", return_value=True),
            patch.object(frappe, "get_doc", return_value=source),
            patch.object(frappe, "get_all", return_value=[row]) as get_all,
            patch.object(frappe.db, "set_value") as update,
            patch.object(frappe, "logger"),
            patch.object(migrate, "database_identity", return_value="new-site"),
        ):
            migrate.rebind_native_site_database()
            update.assert_called_once_with(
                "CEO Company Source", "local-registration", "database_identity", "new-site"
            )
            self.assertEqual(get_all.call_args.kwargs["filters"], {"data_source": "Site DB"})

    def test_native_rebinding_is_idempotent(self):
        with (
            patch.object(frappe.db, "exists", return_value=True),
            patch.object(frappe, "get_doc", return_value=frappe._dict(is_site_db=1)),
            patch.object(
                frappe,
                "get_all",
                return_value=[frappe._dict(name="local", database_identity="current", status="Registered")],
            ),
            patch.object(migrate, "database_identity", return_value="current"),
            patch.object(frappe.db, "set_value") as update,
        ):
            migrate.rebind_native_site_database()
            update.assert_not_called()

    def test_remote_source_cannot_masquerade_as_native_site_db(self):
        with (
            patch.object(frappe.db, "exists", return_value=True),
            patch.object(frappe, "get_doc", return_value=frappe._dict(is_site_db=0)),
            patch.object(frappe.db, "set_value") as update,
        ):
            with self.assertRaises(frappe.ValidationError):
                migrate.rebind_native_site_database()
            update.assert_not_called()

    def test_pending_native_registration_cannot_be_rebound(self):
        with (
            patch.object(frappe.db, "exists", return_value=True),
            patch.object(frappe, "get_doc", return_value=frappe._dict(is_site_db=1)),
            patch.object(
                frappe,
                "get_all",
                return_value=[frappe._dict(name="local", database_identity="old", status="Importing")],
            ),
            patch.object(migrate, "database_identity", return_value="current"),
            patch.object(frappe.db, "set_value") as update,
        ):
            with self.assertRaises(frappe.ValidationError):
                migrate.rebind_native_site_database()
            update.assert_not_called()

    def test_hooks_are_owned_by_insights(self):
        self.assertEqual(hooks.before_migrate, "insights.ceo_finance.migrate.rebind_native_site_database")
        self.assertIn("insights.ceo_finance.api.ensure_hourly_sync", hooks.after_migrate)
        self.assertEqual(
            hooks.doc_events["Insights Query v3"]["validate"],
            "insights.ceo_finance.api.protect_managed_query",
        )
        self.assertEqual(
            hooks.doc_events["Insights Data Source v3"]["validate"],
            "insights.ceo_finance.api.protect_registered_database",
        )

    def test_doctypes_keep_names_and_use_insights_module(self):
        for name in ("CEO Company Source", "CEO Dashboard Settings"):
            path = Path(
                frappe.get_app_path(
                    "insights", "insights", "doctype", frappe.scrub(name), f"{frappe.scrub(name)}.json"
                )
            )
            schema = json.loads(path.read_text())
            self.assertEqual(schema["name"], name)
            self.assertEqual(schema["module"], "Insights")

    def test_unconfigured_site_keeps_standard_sync_frequency(self):
        with patch.object(frappe.db, "exists", side_effect=[True, False]):
            with patch.object(frappe.db, "set_value") as update:
                api.ensure_hourly_sync()
                update.assert_not_called()

    def test_pending_registration_blocks_ownership_transfer(self):
        with patch.object(frappe.db, "exists", return_value=True):
            with patch.object(frappe, "reload_doc") as reload:
                with self.assertRaises(frappe.ValidationError):
                    migrate.migrate_legacy_app()
                reload.assert_not_called()

    def test_transfer_is_idempotent_without_legacy_app(self):
        with (
            patch.object(frappe.db, "exists", side_effect=[True, False]),
            patch.object(frappe, "reload_doc") as reload,
            patch.object(frappe, "get_installed_apps", return_value=["frappe", "insights"]),
            patch.object(frappe, "clear_cache"),
            patch.object(frappe.db, "commit"),
            patch("frappe.installer.remove_from_installed_apps") as detach,
        ):
            migrate.migrate_legacy_app()
            self.assertEqual(reload.call_count, 2)
            detach.assert_not_called()
