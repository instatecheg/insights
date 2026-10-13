from contextlib import nullcontext
from unittest import TestCase
from unittest.mock import Mock, patch

import frappe

from insights.ceo_finance import api


class TestRegistration(TestCase):
    def test_period_can_list_years_only_on_its_bound_query(self):
        dashboard = frappe.get_doc(
            {
                "doctype": "Insights Dashboard v3",
                "items": frappe.as_json(
                    [
                        {
                            "type": "filter",
                            "finance_period": True,
                            "links": {"chart": "`financial`.`posting_date`"},
                        }
                    ]
                ),
            }
        )
        self.assertTrue(dashboard.is_filter_column("financial", "posting_date"))
        self.assertTrue(dashboard.is_filter_column("financial", "report_year"))
        self.assertFalse(dashboard.is_filter_column("other", "report_year"))
        self.assertFalse(dashboard.is_filter_column("financial", "account"))

    def test_regular_date_filter_does_not_expose_additional_columns(self):
        dashboard = frappe.get_doc(
            {
                "doctype": "Insights Dashboard v3",
                "items": frappe.as_json(
                    [
                        {
                            "type": "filter",
                            "links": {"chart": "`financial`.`posting_date`"},
                        }
                    ]
                ),
            }
        )
        self.assertFalse(dashboard.is_filter_column("financial", "report_year"))

    def test_financial_source_preserves_exact_posting_dates_and_vouchers(self):
        sql = api.financial_sql("Local")
        self.assertIn("g.posting_date AS posting_date", sql)
        self.assertIn("g.voucher_no AS voucher_no", sql)
        self.assertIn("g.name AS gl_entry", sql)
        self.assertNotIn("GROUP BY", sql)
        self.assertNotIn("'-01-01'", sql)

    @patch.object(api.frappe, "get_doc")
    def test_future_source_charts_inherit_company_and_date_filters(self, get_doc):
        get_doc.side_effect = [
            frappe._dict(name="combined_chart", query="combined"),
            frappe._dict(name="new_chart", query="new_financial"),
        ]
        items = [
            {"type": "chart", "chart": "combined_chart"},
            {"type": "chart", "chart": "new_chart"},
            {"type": "filter", "filter_name": "Company", "links": {}},
            {"type": "filter", "filter_name": "Reporting Period", "links": {}},
        ]
        api.link_financial_filters(items)
        self.assertEqual(items[2]["links"]["new_chart"], "`new_financial`.`company_label`")
        self.assertEqual(items[3]["links"]["combined_chart"], "`combined`.`posting_date`")

    def test_new_chart_titles_do_not_keep_old_company_label(self):
        self.assertEqual(
            api.company_chart_title("Local - Revenue YTD", "Local", "Mos Star"),
            "Mos Star - Revenue YTD",
        )
        self.assertEqual(
            api.company_chart_title("Net Profit Trend - Local YTD", "Local", "Mos Star"),
            "Net Profit Trend - Mos Star YTD",
        )

    @patch.object(api, "settings")
    @patch.object(api.frappe, "get_doc")
    @patch.object(api.frappe.db, "commit")
    @patch.object(api.frappe.db, "rollback")
    @patch.object(api.frappe, "cache")
    @patch.object(api, "inspect_source")
    @patch.object(api, "save_registration")
    @patch.object(api, "create_company_charts", return_value=[{"type": "text"}])
    @patch.object(api, "registrations")
    @patch.object(api, "update_dashboard")
    @patch.object(api.frappe, "enqueue")
    @patch("insights.ceo_finance.resumable_import.import_next_batch")
    def test_success_imports_all_tables_before_including_source(
        self,
        importer,
        enqueue,
        update,
        rows,
        charts,
        save,
        inspect,
        cache,
        rollback,
        commit,
        get_doc,
        settings,
    ):
        source = frappe._dict(is_site_db=0, host="host", port=3306, database_name="new")
        record = Mock(status="Queued", data_source="new", source_label="New")
        record.database_identity = api.database_identity(source)
        query = Mock(name="query")
        query.name = "new-query"
        table = Mock(before_import_script="", row_limit=0)
        config = frappe._dict(dashboard="dashboard")
        settings.return_value = config
        inspect.return_value = (source, [{"name": "New", "default_currency": "SAR"}])
        cache.lock.return_value = nullcontext()
        rows.return_value = [frappe._dict(status="Registered", financial_query="new-query")]

        def documents(doctype, name=None):
            if isinstance(doctype, dict):
                doc = Mock()
                doc.insert.return_value = query
                return doc
            if doctype == "CEO Company Source":
                return record
            if doctype == "Insights Table v3":
                return table
            return frappe._dict(workbook="1")

        get_doc.side_effect = documents

        def import_tables(registration):
            self.assertEqual(record.status, "Importing")
            update.assert_not_called()
            return True

        importer.side_effect = import_tables
        api.run_registration("record")
        importer.assert_called_once_with(record)
        self.assertEqual(record.status, "Registered")
        self.assertEqual(record.financial_query, "new-query")
        query.execute.assert_called_once_with(force=True, page_size=1000)
        update.assert_called_once_with(config, rows.return_value, charts.return_value)
        self.assertEqual(
            enqueue.call_args.args[0], "insights.ceo_finance.resumable_import.cleanup_registration"
        )

    def test_sar_only(self):
        api.validate_companies([{"name": "A", "default_currency": "SAR"}])

    def test_other_currency_rejected(self):
        with self.assertRaises(frappe.ValidationError):
            api.validate_companies([{"name": "A", "default_currency": "USD"}])

    def test_empty_company_list_rejected(self):
        with self.assertRaises(frappe.ValidationError):
            api.validate_companies([])

    def test_label_quotes_are_escaped(self):
        self.assertIn("'O''Brien'", api.financial_sql("O'Brien"))

    def test_control_characters_rejected(self):
        for label in ("A\\B", "A\nB"):
            with self.assertRaises(frappe.ValidationError):
                api.sql_literal(label)

    def test_accounting_filters(self):
        sql = api.financial_sql("New Source")
        for expected in (
            "g.is_cancelled=0",
            "'Period Closing Voucher'",
            "g.is_opening",
            "g.posting_date <= CURDATE()",
            "a.company=g.company",
            "g.credit-g.debit AS net_profit",
        ):
            self.assertIn(expected, sql)

    def test_database_identity_ignores_user_and_label(self):
        first = frappe._dict(
            is_site_db=0, host="DB.EXAMPLE.COM", port=3306, database_name="ledger", username="one"
        )
        second = frappe._dict(
            is_site_db=0, host="db.example.com", port="3306", database_name="ledger", username="two"
        )
        self.assertEqual(api.database_identity(first), api.database_identity(second))
        second.database_name = "different"
        self.assertNotEqual(api.database_identity(first), api.database_identity(second))

    def test_union_all_has_one_reference_per_source(self):
        rows = [
            frappe._dict(financial_query="local"),
            frappe._dict(financial_query="arena"),
            frappe._dict(financial_query="new"),
        ]
        ops = api.union_operations(rows, "1")
        self.assertEqual([op["type"] for op in ops], ["source", "union", "union"])
        self.assertEqual([op["table"]["query_name"] for op in ops], ["local", "arena", "new"])
        self.assertFalse(ops[1]["distinct"])

    def test_no_sources_rejected(self):
        with self.assertRaises(frappe.ValidationError):
            api.union_operations([], "1")

    def test_chart_references_are_replaced_without_mutating_template(self):
        before = [
            {"type": "source", "table": {"query_name": "old"}},
            {"type": "summarize", "measure": "net_profit"},
        ]
        after = api.replace_query_reference(before, "old", "new")
        self.assertEqual(after[0]["table"]["query_name"], "new")
        self.assertEqual(before[0]["table"]["query_name"], "old")
        self.assertEqual(after[1], before[1])

    @patch.object(api, "has_role", return_value=False)
    def test_admin_access_required(self, _role):
        with self.assertRaises(frappe.PermissionError):
            api.require_admin()

    @patch.object(api, "settings")
    @patch.object(api, "inspect_source")
    @patch.object(api.frappe, "cache")
    @patch.object(api.frappe.db, "get_value")
    @patch.object(api.frappe, "enqueue")
    def test_duplicate_database_cannot_be_registered(self, enqueue, get_value, cache, inspect, _settings):
        inspect.return_value = (
            frappe._dict(is_site_db=0, host="host", port=3306, database_name="db"),
            [{"name": "A", "default_currency": "SAR"}],
        )
        cache.lock.return_value = nullcontext()
        get_value.return_value = frappe._dict(name="existing", status="Registered")
        with self.assertRaisesRegex(frappe.ValidationError, "already listed"):
            api.register_source("source", "Label")
        enqueue.assert_not_called()

    @patch.object(api, "settings")
    @patch.object(api, "save_registration")
    @patch.object(api.frappe, "get_doc")
    @patch.object(api.frappe.db, "rollback")
    @patch.object(api.frappe.db, "commit")
    @patch.object(api.frappe, "log_error")
    @patch.object(api, "inspect_source", side_effect=RuntimeError("connection failure"))
    @patch.object(api, "update_dashboard")
    @patch.object(api.frappe, "cache")
    def test_import_failure_does_not_change_dashboard(
        self, cache, update, _inspect, log_error, _commit, _rollback, get_doc, save, _settings
    ):
        cache.lock.return_value = nullcontext()
        record = Mock(status="Queued", data_source="source")
        get_doc.return_value = record
        with self.assertRaisesRegex(RuntimeError, "connection failure"):
            api.run_registration("record")
        self.assertEqual(record.status, "Failed")
        update.assert_not_called()
        log_error.assert_called_once()
        save.assert_called_once_with(record)

    @patch.object(api.frappe.db, "exists", return_value=True)
    @patch.object(api.frappe.db, "get_value")
    def test_registered_database_cannot_be_repointed(self, get_value, _exists):
        doc = frappe._dict(name="registered", is_site_db=0, host="host", port=3306, database_name="original")
        get_value.return_value = frappe._dict(
            name="registered", database_identity=api.database_identity(doc), status="Registered"
        )
        doc.database_name = "different"
        with self.assertRaisesRegex(frappe.ValidationError, "cannot be changed"):
            api.protect_registered_database(doc)

    @patch.object(api.frappe, "logger")
    @patch.object(api.frappe.db, "set_value")
    @patch.object(api.frappe.db, "exists", return_value=True)
    @patch.object(api.frappe.db, "get_value")
    def test_restored_native_site_db_rebinds_during_fixture_migration(
        self, get_value, _exists, set_value, _logger
    ):
        doc = frappe._dict(name="Site DB", is_site_db=1)
        get_value.return_value = frappe._dict(
            name="local-registration", database_identity="identity-from-source-site", status="Registered"
        )
        with patch.object(frappe, "flags", frappe._dict(in_migrate=True)):
            api.protect_registered_database(doc)
        self.assertEqual(
            set_value.call_args.args,
            (
                "CEO Company Source",
                "local-registration",
                "database_identity",
                api.database_identity(doc),
            ),
        )

    @patch.object(api.frappe.db, "exists", return_value=True)
    @patch.object(api.frappe.db, "get_value")
    def test_remote_identity_cannot_be_rebound_during_migration(self, get_value, _exists):
        doc = frappe._dict(name="Arena DB", is_site_db=0, host="remote", port=3306, database_name="ledger")
        get_value.return_value = frappe._dict(
            name="arena-registration",
            database_identity="identity-from-source-site",
            status="Registered",
        )
        with patch.object(frappe, "flags", frappe._dict(in_migrate=True)):
            with self.assertRaisesRegex(frappe.ValidationError, "cannot be changed"):
                api.protect_registered_database(doc)

    def test_json_lists_are_serialized_for_frappe_documents(self):
        record = frappe.get_doc(
            {
                "doctype": "CEO Company Source",
                "source_label": "Example",
                "data_source": "Site DB",
                "database_identity": "test",
                "companies": [{"name": "Example", "default_currency": "SAR"}],
                "dashboard_items": ["card"],
            }
        )
        with patch.object(record, "save"):
            api.save_registration(record)
        self.assertIsInstance(record.companies, str)
        self.assertEqual(frappe.parse_json(record.dashboard_items), ["card"])

    @patch.object(api.frappe.db, "exists", return_value=True)
    @patch.object(api.frappe.db, "get_value")
    def test_financial_source_cannot_change(self, get_value, _exists):
        get_value.return_value = frappe._dict(data_source="local", source_label="Local")
        doc = frappe._dict(
            name="financial",
            operations=[{"type": "sql", "data_source": "arena", "raw_sql": api.financial_sql("Local")}],
        )
        with self.assertRaisesRegex(frappe.ValidationError, "managed"):
            api.protect_managed_query(doc)
