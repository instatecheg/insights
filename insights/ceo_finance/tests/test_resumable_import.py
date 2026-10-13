import tempfile
from contextlib import ExitStack, nullcontext
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch

import frappe
import ibis
import pandas as pd
from rq.job import JobStatus

import insights
from insights.ceo_finance import api
from insights.ceo_finance import resumable_import as importer


class TestResumableImport(TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.directory = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.path = str(Path(self.directory) / "checkpoint.duckdb")
        self.db = ibis.duckdb.connect(self.path)
        self.addCleanup(lambda: self.db.disconnect())
        self.schema = ibis.schema({"name": "string", "creation": "timestamp(6)", "amount": "int64"})
        self.stage = importer.stage_schema("registration")
        importer.ensure_stage(self.db, self.stage)
        self.stack.enter_context(patch.object(importer, "BATCH_SIZE", 2))
        warehouse = self.stack.enter_context(patch.object(insights, "warehouse"))
        warehouse.get_write_connection.side_effect = lambda **kwargs: nullcontext(self.db)
        self.stack.enter_context(patch.object(frappe.utils, "now", return_value="2026-10-13 18:00:00"))

    def data(self, names):
        return pd.DataFrame(
            {
                "name": names,
                "creation": [pd.Timestamp("2020-01-01")] * len(names),
                "amount": list(range(len(names))),
            }
        )

    def append(self, names, last=None):
        return importer.append_batch(
            self.db,
            self.stage,
            "tabGL Entry",
            self.data(names),
            self.schema,
            "identity",
            "z",
            last,
        )

    def test_checkpoint_and_rows_survive_backend_restart(self):
        self.assertEqual(self.append(["a", "b"]), 2)
        self.db.disconnect()
        self.db = ibis.duckdb.connect(self.path)
        state = importer.checkpoint(self.db, self.stage, "tabGL Entry")
        self.assertEqual(state.last_key, "b")
        self.assertEqual(state.rows_imported, 2)
        self.assertFalse(state.complete)
        self.assertEqual(self.append(["c"], last="b"), 3)
        self.assertTrue(importer.checkpoint(self.db, self.stage, "tabGL Entry").complete)
        names = self.db.table("tabGL Entry", database=self.stage).execute()["name"].tolist()
        self.assertEqual(names, ["a", "b", "c"])

    def test_checkpoint_failure_rolls_back_inserted_batch(self):
        self.append(["a", "b"])
        original = importer.checkpoint
        with patch.object(importer, "checkpoint", side_effect=RuntimeError("interrupted")):
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                self.append(["c"], last="b")
        self.assertEqual(original(self.db, self.stage, "tabGL Entry").rows_imported, 2)
        self.assertEqual(self.db.table("tabGL Entry", database=self.stage).count().execute(), 2)
        self.assertEqual(self.append(["c"], last="b"), 3)

    def test_interrupt_between_insert_and_checkpoint_is_atomic(self):
        self.append(["a", "b"])
        original = self.db.insert

        def interrupted(*args, **kwargs):
            original(*args, **kwargs)
            raise KeyboardInterrupt

        with patch.object(self.db, "insert", side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):
                self.append(["c"], last="b")
        self.assertEqual(importer.checkpoint(self.db, self.stage, "tabGL Entry").last_key, "b")
        self.assertEqual(self.db.table("tabGL Entry", database=self.stage).count().execute(), 2)

    def test_replayed_batch_is_rejected_without_duplicates(self):
        self.append(["a", "b"])
        with self.assertRaisesRegex(RuntimeError, "checkpoint changed"):
            self.append(["a", "b"])
        self.assertEqual(self.db.table("tabGL Entry", database=self.stage).count().execute(), 2)

    def test_empty_source_is_completed_with_schema(self):
        self.assertEqual(self.append([]), 0)
        state = importer.checkpoint(self.db, self.stage, "tabGL Entry")
        self.assertTrue(state.complete)
        self.assertEqual(self.db.table("tabGL Entry", database=self.stage).schema(), self.schema)

    def prepare_source(self):
        for table in api.TABLE_COLUMNS:
            self.db.create_table(
                "remote_" + frappe.scrub(table), self.data(["a", "b", "c"]), schema=self.schema
            )
        source = Mock()
        source.get_ibis_table.side_effect = lambda table: self.db.table("remote_" + frappe.scrub(table))
        record = Mock(data_source="source", database_identity="identity")
        record.name = "registration"
        self.stack.enter_context(patch.object(importer, "db_connections", return_value=nullcontext()))
        self.stack.enter_context(patch.object(importer, "database_identity", return_value="identity"))
        self.stack.enter_context(patch.object(importer, "source_doc", return_value=source))
        self.table_doc = Mock(row_limit=0, before_import_script="")
        self.stack.enter_context(
            patch.object(
                frappe,
                "get_doc",
                side_effect=lambda dt, name: source if dt == "Insights Data Source v3" else self.table_doc,
            )
        )
        return record

    def test_one_batch_per_job_and_identical_creation_dates_do_not_skip_rows(self):
        record = self.prepare_source()
        with patch.object(importer, "publish") as publish:
            for _ in range(6):
                self.assertFalse(importer.import_next_batch(record))
            self.assertTrue(importer.import_next_batch(record))
            publish.assert_called_once()
        for table in api.TABLE_COLUMNS:
            state = importer.checkpoint(self.db, self.stage, table)
            self.assertEqual(state.rows_imported, 3)
            self.assertTrue(state.complete)
        self.assertIn("3 rows saved", record.db_set.call_args.args[1])

    def test_row_limit_is_rejected_instead_of_silently_truncating(self):
        record = self.prepare_source()
        self.table_doc.row_limit = 100
        with self.assertRaises(frappe.ValidationError):
            importer.import_next_batch(record)
        self.assertIsNone(importer.checkpoint(self.db, self.stage, "tabGL Entry"))

    def test_global_row_cap_is_not_used(self):
        record = self.prepare_source()
        with (
            patch.object(frappe.db, "get_single_value", return_value=1) as cap,
            patch.object(importer, "publish"),
        ):
            for _ in range(7):
                importer.import_next_batch(record)
            cap.assert_not_called()
        self.assertEqual(importer.checkpoint(self.db, self.stage, "tabGL Entry").rows_imported, 3)

    def test_incomplete_dataset_cannot_be_published(self):
        with self.assertRaisesRegex(RuntimeError, "incomplete"):
            importer.publish(Mock(), self.stage)

    def test_publish_verifies_unique_rows_before_replacing_existing_data(self):
        for table in api.TABLE_COLUMNS:
            importer.append_batch(
                self.db,
                self.stage,
                table,
                self.data(["a"]),
                self.schema,
                "identity",
                "z",
                None,
            )
        self.db.insert(
            "tabGL Entry", ibis.memtable(self.data(["a"]), schema=self.schema), database=self.stage
        )
        self.db.create_database("live")
        self.db.create_table("gl", self.data(["old"]), schema=self.schema, database="live")
        with self.assertRaisesRegex(RuntimeError, "verification failed"):
            importer.publish(Mock(database_identity="identity"), self.stage)
        self.assertEqual(self.db.table("gl", database="live").execute()["name"].tolist(), ["old"])

    def stage_completed_tables(self):
        for table in api.TABLE_COLUMNS:
            importer.append_batch(
                self.db,
                self.stage,
                table,
                self.data(["a"]),
                self.schema,
                "identity",
                "z",
                None,
                target_schema="source",
            )
            self.db.create_table(
                frappe.scrub(table),
                self.data(["old"]),
                schema=self.schema,
                database="source",
            )

    def test_atomic_publication_can_be_repeated_after_worker_interruption(self):
        self.stage_completed_tables()
        record = Mock(database_identity="identity", data_source="source")
        with (
            patch.object(frappe, "get_doc", return_value=Mock()),
            patch.object(frappe.db, "commit"),
        ):
            importer.publish(record, self.stage)
            importer.publish(record, self.stage)
        for table in api.TABLE_COLUMNS:
            self.assertEqual(
                self.db.table(frappe.scrub(table), database="source").execute()["name"].tolist(), ["a"]
            )
            self.assertTrue(importer.checkpoint(self.db, self.stage, table).published)

    def test_failure_after_first_table_rename_rolls_back_all_publication(self):
        self.stage_completed_tables()
        state = importer.checkpoint(self.db, self.stage, "tabAccount")
        self.db.insert(
            state.staged_table,
            ibis.memtable(self.data(["a"]), schema=self.schema),
            database="source",
        )
        with self.assertRaisesRegex(RuntimeError, "verification failed"):
            importer.publish(Mock(database_identity="identity", data_source="source"), self.stage)
        for table in api.TABLE_COLUMNS:
            self.assertEqual(
                self.db.table(frappe.scrub(table), database="source").execute()["name"].tolist(), ["old"]
            )
            self.assertFalse(importer.checkpoint(self.db, self.stage, table).published)

    def test_cleanup_does_not_delete_live_tables(self):
        self.stage_completed_tables()
        record = Mock(database_identity="identity", data_source="source", status="Registered")
        record.name = "registration"
        with (
            patch.object(frappe, "get_doc", return_value=record),
            patch.object(frappe.db, "commit"),
        ):
            importer.publish(record, self.stage)
            importer.cleanup_registration("registration")
        for table in api.TABLE_COLUMNS:
            self.assertEqual(self.db.table(frappe.scrub(table), database="source").count().execute(), 1)
        self.assertNotIn(self.stage, self.db.list_databases())

    def test_unfinished_registration_checkpoints_cannot_be_deleted(self):
        with patch.object(frappe, "get_doc", return_value=Mock(status="Importing")):
            with self.assertRaises(frappe.ValidationError):
                importer.cleanup_registration("registration")

    def test_changed_remote_schema_refuses_resume(self):
        record = self.prepare_source()
        importer.import_next_batch(record)
        self.db.raw_sql("ALTER TABLE remote_tabgl_entry ADD COLUMN extra VARCHAR")
        with self.assertRaises(frappe.ValidationError):
            importer.import_next_batch(record)
        self.assertEqual(importer.checkpoint(self.db, self.stage, "tabGL Entry").rows_imported, 2)

    def test_complete_history_exceeds_one_million_rows_without_truncation(self):
        record = self.prepare_source()
        total = 1_000_003
        data = pd.DataFrame(
            {
                "name": [f"row{index:07d}" for index in range(total)],
                "creation": pd.Timestamp("2020-01-01"),
                "amount": 1,
            }
        )
        self.db.create_table("remote_tabgl_entry", data, schema=self.schema, overwrite=True)
        del data
        with (
            patch.object(importer, "BATCH_SIZE", 5000),
            patch.object(importer, "TABLE_COLUMNS", {"tabGL Entry": api.TABLE_COLUMNS["tabGL Entry"]}),
            patch.object(importer, "publish"),
        ):
            for _ in range(201):
                self.assertFalse(importer.import_next_batch(record))
            self.assertTrue(importer.import_next_batch(record))
        state = importer.checkpoint(self.db, self.stage, "tabGL Entry")
        self.assertEqual(state.rows_imported, total)
        rows, unique_rows, amount = self.db.con.execute(
            f"SELECT COUNT(*), COUNT(DISTINCT name), SUM(amount) FROM "
            f"{importer.identifier(state.target_schema)}.{importer.identifier(state.staged_table)}"
        ).fetchone()
        self.assertEqual((rows, unique_rows, amount), (total, total, total))


class TestRegistrationContinuation(TestCase):
    def test_pending_batch_queues_continuation_without_changing_dashboard(self):
        record = Mock(status="Importing")
        with (
            patch.object(api, "settings"),
            patch.object(frappe, "get_doc", return_value=record),
            patch.object(importer, "import_next_batch", return_value=False),
            patch.object(api, "enqueue_registration") as enqueue,
            patch.object(frappe.db, "commit"),
            patch.object(api, "update_dashboard") as update,
        ):
            api._run_registration("record")
        enqueue.assert_called_once_with(record)
        update.assert_not_called()
        self.assertEqual(record.status, "Importing")

    def test_current_batch_job_is_used_for_failure_detection(self):
        record = frappe._dict(status="Importing", name="record", import_job="batch-123")
        job = Mock()
        job.get_status.return_value = JobStatus.FAILED
        with patch("frappe.utils.background_jobs.get_job", return_value=job) as get_job:
            self.assertTrue(api.failed_registration_job(record))
        get_job.assert_called_once_with("batch-123")

    def test_active_job_cannot_be_deleted_by_retry(self):
        record = Mock(status="Failed")
        record.get.return_value = "batch-123"
        job = Mock()
        job.get_status.return_value = JobStatus.STARTED
        with (
            patch.object(api, "settings"),
            patch.object(frappe, "cache") as cache,
            patch.object(frappe, "get_doc", return_value=record),
            patch("frappe.utils.background_jobs.get_job", return_value=job),
            patch.object(api, "enqueue_registration") as enqueue,
        ):
            cache.lock.return_value = nullcontext()
            with self.assertRaises(frappe.ValidationError):
                api.retry_registration("record")
        job.delete.assert_not_called()
        enqueue.assert_not_called()

    def test_continuations_have_distinct_job_ids_and_short_timeouts(self):
        record = Mock()
        record.name = "record"
        with (
            patch.object(frappe, "generate_hash", side_effect=["first", "second"]),
            patch.object(api, "save_registration"),
            patch.object(frappe, "enqueue") as enqueue,
        ):
            api.enqueue_registration(record)
            api.enqueue_registration(record)
        self.assertNotEqual(
            enqueue.call_args_list[0].kwargs["job_id"], enqueue.call_args_list[1].kwargs["job_id"]
        )
        self.assertEqual(enqueue.call_args.kwargs["timeout"], 120)
        self.assertTrue(enqueue.call_args.kwargs["enqueue_after_commit"])


class TestManagedImportSettings(TestCase):
    def make_importer(self):
        from insights.insights.doctype.insights_data_source_v3.data_warehouse import WarehouseTableImporter

        result = WarehouseTableImporter(Mock(table_name="tabGL Entry", data_source="source"))
        result.log = Mock()
        result._log = Mock()
        return result

    def test_managed_refresh_bypasses_global_cap_and_uses_unique_key(self):
        importer = self.make_importer()
        settings = frappe._dict(row_limit=0, before_import_script="", sync_mode="Full")
        with (
            patch.object(frappe, "get_value", return_value=settings),
            patch.object(frappe.db, "get_single_value", return_value=1),
            patch.object(frappe.db, "exists", return_value=True),
        ):
            importer.prepare_settings()
        self.assertEqual(importer.settings.row_limit, 0)
        importer.remote_table = Mock()
        with patch.object(importer, "apply_limit") as limit:
            importer._prepare_full_table()
        self.assertEqual(importer.cursor_column, "name")
        limit.assert_not_called()

    def test_unmanaged_import_retains_existing_global_cap(self):
        importer = self.make_importer()
        settings = frappe._dict(row_limit=0, before_import_script="", sync_mode="Full")
        with (
            patch.object(frappe, "get_value", return_value=settings),
            patch.object(frappe.db, "get_single_value", return_value=100),
            patch.object(frappe.db, "exists", return_value=False),
        ):
            importer.prepare_settings()
        self.assertEqual(importer.settings.row_limit, 100)
        self.assertFalse(importer.settings.complete_financial_history)

    def test_managed_import_rejects_explicit_truncation(self):
        importer = self.make_importer()
        with (
            patch.object(
                frappe, "get_value", return_value=frappe._dict(row_limit=1, before_import_script="")
            ),
            patch.object(frappe.db, "get_single_value", return_value=512),
            patch.object(frappe.db, "exists", return_value=True),
        ):
            with self.assertRaises(frappe.ValidationError):
                importer.prepare_settings()

    def test_managed_refresh_batch_size_is_bounded(self):
        importer = self.make_importer()
        importer.settings = frappe._dict(memory_limit=512, complete_financial_history=True)
        importer.remote_table = Mock()
        importer.remote_table.head.return_value.execute.return_value = pd.DataFrame({"name": ["small"] * 10})
        self.assertEqual(importer.calculate_batch_size(), 5000)
