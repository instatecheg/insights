from contextlib import nullcontext
from unittest import TestCase
from unittest.mock import MagicMock, patch

import frappe
from rq.job import JobStatus

from insights.api import dashboard_sync


class TestDashboardSync(TestCase):
    def test_sources_are_deduplicated_and_live_charts_are_skipped(self):
        dashboard = MagicMock(
            items=[
                {"type": "text"},
                {"type": "chart", "chart": "first"},
                {"type": "chart", "chart": "second"},
                {"type": "chart", "chart": "live"},
            ]
        )
        charts = {name: MagicMock(data_query=name, title=name) for name in ("first", "second", "live")}
        refs = [
            {"data_source": "Local", "table_name": "GL"},
            {"data_source": "Arena", "table_name": "GL"},
        ]
        queries = {name: MagicMock(use_live_connection=name == "live") for name in charts}
        for query in queries.values():
            query.get_source_tables.return_value = refs
        with (
            patch.object(
                frappe,
                "get_doc",
                side_effect=lambda dt, name: charts[name] if dt == "Insights Chart v3" else queries[name],
            ),
            patch.object(frappe.db, "get_value", return_value="Database"),
            patch(
                "insights.insights.doctype.insights_team.insights_team.check_table_permission"
            ) as permission,
        ):
            self.assertEqual(dashboard_sync._source_tables(dashboard), [("Arena", "GL"), ("Local", "GL")])
        queries["live"].get_source_tables.assert_not_called()
        self.assertEqual(permission.call_count, 4)

    def test_unresolved_sources_are_an_error(self):
        dashboard = MagicMock(items=[{"type": "chart", "chart": "first"}])
        chart = MagicMock(data_query="data", title="Script")
        query = MagicMock(use_live_connection=False)
        query.get_source_tables.return_value = []
        with patch.object(frappe, "get_doc", side_effect=[chart, query]):
            with self.assertRaises(frappe.ValidationError):
                dashboard_sync._source_tables(dashboard)

    def test_all_archived_financial_sources_need_no_import(self):
        dashboard = MagicMock(items=[{"type": "chart", "chart": "summary"}])
        dashboard.name = "main"
        chart = MagicMock(data_query="data", query="combined")
        query = MagicMock(use_live_connection=False)
        query.get_source_tables.return_value = []
        with (
            patch.object(frappe, "get_doc", side_effect=[chart, query]),
            patch.object(
                frappe, "get_single", return_value=frappe._dict(dashboard="main", combined_query="combined")
            ),
            patch.object(frappe.db, "exists", side_effect=[True, True, False]),
        ):
            self.assertEqual(dashboard_sync._source_tables(dashboard), [])

    def test_non_admin_cannot_sync(self):
        with patch("insights.decorators.has_role", return_value=False):
            with self.assertRaises(frappe.PermissionError):
                dashboard_sync._dashboard("dashboard")

    def test_disabled_dashboard_cannot_sync(self):
        with (
            patch("insights.decorators.has_role", return_value=True),
            patch.object(frappe, "has_permission"),
            patch.object(frappe, "get_doc", return_value=MagicMock(sync_on_refresh=False)),
        ):
            with self.assertRaises(frappe.ValidationError):
                dashboard_sync._dashboard("dashboard")

    def test_existing_import_job_is_reused(self):
        job = MagicMock()
        job.get_status.return_value = JobStatus.STARTED
        with (
            patch.object(dashboard_sync, "_dashboard"),
            patch.object(dashboard_sync, "_source_tables"),
            patch.object(frappe.cache, "lock", return_value=nullcontext()),
            patch("frappe.utils.background_jobs.get_job", return_value=job),
            patch.object(frappe, "enqueue") as enqueue,
        ):
            self.assertEqual(
                dashboard_sync.start_dashboard_sync("dashboard"),
                {"job_id": "insights_dashboard_sync_dashboard"},
            )
            enqueue.assert_not_called()

    def test_failed_job_is_not_reported_as_completed(self):
        job = MagicMock(kwargs={"kwargs": {"dashboard_name": "dashboard"}})
        job.get_status.return_value = JobStatus.FAILED
        with (
            patch.object(dashboard_sync, "_dashboard"),
            patch("frappe.utils.background_jobs.get_job", return_value=job),
        ):
            with self.assertRaises(frappe.ValidationError):
                dashboard_sync.get_dashboard_sync_status("dashboard", "insights_dashboard_sync_dashboard")

    def test_finished_job_is_reported(self):
        job = MagicMock(kwargs={"kwargs": {"dashboard_name": "dashboard"}})
        job.get_status.return_value = JobStatus.FINISHED
        with (
            patch.object(dashboard_sync, "_dashboard"),
            patch("frappe.utils.background_jobs.get_job", return_value=job),
        ):
            self.assertEqual(
                dashboard_sync.get_dashboard_sync_status("dashboard", "insights_dashboard_sync_dashboard"),
                {"status": "finished"},
            )

    def test_job_for_another_dashboard_is_rejected(self):
        with patch.object(dashboard_sync, "_dashboard"):
            with self.assertRaises(frappe.ValidationError):
                dashboard_sync.get_dashboard_sync_status("dashboard", "insights_dashboard_sync_other")

    def test_missing_job_is_an_error(self):
        with (
            patch.object(dashboard_sync, "_dashboard"),
            patch("frappe.utils.background_jobs.get_job", return_value=None),
        ):
            with self.assertRaises(frappe.ValidationError):
                dashboard_sync.get_dashboard_sync_status("dashboard", "insights_dashboard_sync_dashboard")

    def test_new_job_is_queued_on_long_queue(self):
        with (
            patch.object(dashboard_sync, "_dashboard"),
            patch.object(dashboard_sync, "_source_tables"),
            patch.object(frappe.cache, "lock", return_value=nullcontext()),
            patch("frappe.utils.background_jobs.get_job", return_value=None),
            patch.object(frappe, "enqueue") as enqueue,
        ):
            dashboard_sync.start_dashboard_sync("dashboard")
            enqueue.assert_called_once_with(
                dashboard_sync.run_dashboard_sync,
                dashboard_name="dashboard",
                queue="long",
                timeout=1800,
                job_id="insights_dashboard_sync_dashboard",
                at_front=True,
            )

    def test_worker_imports_every_table_before_returning(self):
        with (
            patch.object(dashboard_sync, "_dashboard"),
            patch.object(dashboard_sync, "_source_tables", return_value=[("Arena", "GL"), ("Local", "GL")]),
            patch.object(frappe.cache, "lock", return_value=nullcontext()),
            patch(
                "insights.insights.doctype.insights_data_source_v3.data_warehouse.execute_warehouse_table_import"
            ) as execute,
        ):
            self.assertEqual(dashboard_sync.run_dashboard_sync("dashboard"), {"tables_synced": 2})
            self.assertEqual(execute.call_count, 2)

    def test_worker_propagates_import_errors_and_stops(self):
        with (
            patch.object(dashboard_sync, "_dashboard"),
            patch.object(dashboard_sync, "_source_tables", return_value=[("Arena", "GL"), ("Local", "GL")]),
            patch.object(frappe.cache, "lock", return_value=nullcontext()),
            patch.object(frappe, "log_error") as log,
            patch(
                "insights.insights.doctype.insights_data_source_v3.data_warehouse.execute_warehouse_table_import",
                side_effect=RuntimeError("Connection failed"),
            ) as execute,
        ):
            with self.assertRaisesRegex(RuntimeError, "Connection failed"):
                dashboard_sync.run_dashboard_sync("dashboard")
            execute.assert_called_once_with("Arena", "GL")
            log.assert_called_once()
