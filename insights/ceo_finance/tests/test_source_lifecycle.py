from contextlib import ExitStack, nullcontext
from copy import deepcopy
from unittest import TestCase
from unittest.mock import Mock, patch

import duckdb
import frappe
import sqlglot

from insights.ceo_finance import api


class TestSourceLifecycle(TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.record = Mock(
            status="Registered",
            financial_query="finance",
            data_source="arena",
            database_identity="identity",
            dashboard_items="[]",
            archived_items="[]",
        )
        self.config = frappe._dict(dashboard="main", technical_dashboard=None)
        self.items = [
            {"type": "text", "text": "<h3>Arena</h3>", "layout": {"i": "heading", "y": 0, "h": 1}},
            {"type": "chart", "chart": "card", "layout": {"i": "card", "y": 1, "h": 2}},
            {"type": "chart", "chart": "other", "layout": {"i": "other", "y": 4, "h": 2}},
        ]
        self.dashboard = Mock(items=deepcopy(self.items))
        self.stack.enter_context(patch.object(api, "settings", return_value=self.config))
        cache = self.stack.enter_context(patch.object(api.frappe, "cache"))
        cache.lock.side_effect = lambda *args, **kwargs: nullcontext()
        self.stack.enter_context(patch.object(api.frappe, "get_doc", side_effect=self.get_doc))
        self.pending = self.stack.enter_context(patch.object(api.frappe.db, "exists", return_value=False))
        self.stack.enter_context(patch("frappe.utils.background_jobs.get_job", return_value=None))
        self.stack.enter_context(patch.object(api.frappe, "get_all", return_value=[]))
        self.stack.enter_context(patch.object(api, "registrations", side_effect=self.registrations))
        self.save = self.stack.enter_context(patch.object(api, "save_registration"))
        self.update = self.stack.enter_context(patch.object(api, "update_dashboard"))
        self.inspect = self.stack.enter_context(
            patch.object(api, "inspect_source", return_value=(Mock(), []))
        )
        self.identity = self.stack.enter_context(
            patch.object(api, "database_identity", return_value="identity")
        )

    def get_doc(self, doctype, name):
        if doctype == "CEO Company Source":
            return self.record
        if doctype == "Insights Dashboard v3":
            return self.dashboard
        if doctype == "Insights Chart v3":
            return frappe._dict(query="finance" if name == "card" else "unrelated")
        raise AssertionError((doctype, name))

    def registrations(self):
        return [
            frappe._dict(
                status=self.record.status,
                financial_query="finance",
                source_label="Arena",
                data_source="arena",
            )
        ]

    def test_disconnect_retains_other_cards_and_archives_heading(self):
        api.disconnect_source("record")
        self.assertEqual(self.record.status, "Archived")
        self.assertEqual(frappe.parse_json(self.dashboard.items), self.items[2:])
        archived = frappe.parse_json(self.record.archived_items)
        self.assertEqual({entry["item"]["layout"]["i"] for entry in archived}, {"heading", "card"})
        self.update.assert_called_once_with(self.config, [])
        self.assertEqual(self.record.financial_query, "finance")
        self.assertEqual(self.record.data_source, "arena")

    def test_reactivation_restores_once_below_other_cards(self):
        api.disconnect_source("record")
        api.reactivate_source("record")
        self.assertEqual(self.record.status, "Registered")
        self.assertEqual(len(frappe.parse_json(self.dashboard.items)), 3)
        self.assertEqual(self.record.archived_items, [])
        restored = {item["layout"]["i"]: item for item in frappe.parse_json(self.dashboard.items)}
        self.assertEqual(restored["heading"]["layout"]["y"], 7)
        self.assertEqual(restored["card"]["layout"]["y"], 8)
        with self.assertRaises(frappe.ValidationError):
            api.reactivate_source("record")
        self.assertEqual(len(frappe.parse_json(self.dashboard.items)), 3)

    def test_invalid_disconnect_does_not_save(self):
        self.record.status = "Archived"
        with self.assertRaises(frappe.ValidationError):
            api.disconnect_source("record")
        self.save.assert_not_called()

    def test_identity_change_does_not_reactivate(self):
        self.record.status = "Archived"
        self.identity.return_value = "different"
        with self.assertRaises(frappe.ValidationError):
            api.reactivate_source("record")
        self.assertEqual(self.record.status, "Archived")
        self.save.assert_not_called()

    def test_connection_failure_does_not_reactivate(self):
        self.record.status = "Archived"
        self.inspect.side_effect = ConnectionError("unavailable")
        with self.assertRaises(ConnectionError):
            api.reactivate_source("record")
        self.assertEqual(self.record.status, "Archived")
        self.save.assert_not_called()

    def test_pending_registration_blocks_membership_changes(self):
        self.pending.return_value = True
        with self.assertRaises(frappe.ValidationError):
            api.disconnect_source("record")
        self.save.assert_not_called()

    def test_empty_combined_preserves_financial_columns(self):
        operations = api.combined_operations([], "1")
        self.assertEqual(operations[0]["type"], "sql")
        self.assertEqual(operations[0]["data_source"], "Site DB")
        self.assertIn("WHERE 1=0", operations[0]["raw_sql"])
        for column in ("company", "posting_date", "account", "total_income", "total_expense", "net_profit"):
            self.assertIn(f" AS {column}", operations[0]["raw_sql"])

    def test_active_combined_references_only_active_queries(self):
        operations = api.combined_operations(self.registrations(), "1")
        self.assertEqual(operations[0]["table"]["query_name"], "finance")
        self.assertEqual(operations[0]["table"]["workbook"], "1")

    def test_archived_sources_are_excluded_from_live_profit_and_loss(self):
        from insights.ceo_finance import detailed_profit_and_loss

        self.record.status = "Archived"
        with (
            patch.object(detailed_profit_and_loss, "registrations", side_effect=self.registrations),
            patch.object(api.frappe.db, "get_single_value", return_value="main"),
        ):
            self.assertEqual(detailed_profit_and_loss.sources("main"), [])

    def test_empty_combined_executes_in_warehouse_with_exact_schema(self):
        sql = api.combined_operations([], "1")[0]["raw_sql"]
        expected = sqlglot.parse_one(api.financial_sql("Arena"), read="mysql").named_selects
        with duckdb.connect(":memory:") as connection:
            result = connection.execute(sqlglot.transpile(sql, read="mysql", write="duckdb")[0])
            self.assertEqual([column[0] for column in result.description], expected)
            self.assertEqual(result.fetchall(), [])

    def test_refresh_in_progress_blocks_disconnect(self):
        from rq.job import JobStatus

        job = Mock()
        job.get_status.return_value = JobStatus.STARTED
        with patch("frappe.utils.background_jobs.get_job", return_value=job):
            with self.assertRaises(frappe.ValidationError):
                api.disconnect_source("record")
        self.save.assert_not_called()

    def test_permission_denial_does_not_change_membership(self):
        self.record.check_permission.side_effect = frappe.PermissionError
        with self.assertRaises(frappe.PermissionError):
            api.disconnect_source("record")
        self.assertEqual(self.record.status, "Registered")
        self.save.assert_not_called()

    def test_shared_cards_wait_for_all_sources(self):
        self.record.archived_items = [
            {
                "dashboard": "main",
                "item": deepcopy(self.items[1]),
                "sources": ["arena", "other"],
            }
        ]
        self.dashboard.items = []
        api.frappe.get_all.side_effect = lambda *args, **kwargs: (
            ["other"] if kwargs.get("pluck") == "data_source" else []
        )
        api.restore_archived_items(self.record)
        self.assertEqual(self.dashboard.items, [])
        self.assertEqual(len(self.record.archived_items), 1)
        api.frappe.get_all.side_effect = None
        api.frappe.get_all.return_value = []
        api.restore_archived_items(self.record)
        self.assertEqual(len(frappe.parse_json(self.dashboard.items)), 1)
        self.assertEqual(self.record.archived_items, [])

    def test_archived_template_can_create_new_source_cards(self):
        self.config.template_query = "template"
        self.record.source_label = "New"
        template = Mock(
            chart_type="Number", title="Old - Income", data_query="template-data", query="template"
        )
        template.name = "template-card"
        data_query = Mock(
            operations=[
                {
                    "type": "source",
                    "table": {"type": "query", "query_name": "template"},
                }
            ]
        )
        data_query.name = "new-data"
        chart = Mock()
        chart.name = "new-card"
        api.frappe.get_doc.side_effect = lambda doctype, name: {
            "Insights Dashboard v3": Mock(items=[]),
            "Insights Chart v3": template,
            "Insights Query v3": data_query,
        }[doctype]
        with (
            patch.object(
                api.frappe.db,
                "get_value",
                side_effect=[
                    [{"dashboard": "main", "item": {"type": "chart", "chart": "template-card"}}],
                    "Old",
                ],
            ),
            patch.object(api.frappe, "copy_doc", side_effect=[data_query, chart]),
            patch.object(api.frappe, "generate_hash", side_effect=["new-heading", "new-card-item"]),
        ):
            items = api.create_company_charts(self.record, self.config, frappe._dict(name="new-query"))
        self.assertEqual(len(items), 2)
        self.assertEqual(chart.query, "new-query")
        self.assertEqual(data_query.operations[0]["table"]["query_name"], "new-query")
