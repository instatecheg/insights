from unittest import TestCase

import frappe


class TestQuerySourceBinding(TestCase):
    def query(self, source="Site DB", locked="Site DB", native=True):
        return frappe.get_doc(
            {
                "doctype": "Insights Query v3",
                "title": "Local Financials",
                "workbook": "1",
                "is_native_query": native,
                "locked_data_source": locked,
                "operations": [{"type": "sql", "data_source": source, "raw_sql": "SELECT 1"}],
            }
        )

    def test_bound_source_is_accepted(self):
        self.query()._validate_locked_data_source()

    def test_wrong_source_is_rejected_on_save(self):
        with self.assertRaisesRegex(frappe.ValidationError, "locked to data source Site DB"):
            self.query(source="Arena").validate()

    def test_wrong_source_is_rejected_before_execution(self):
        with self.assertRaisesRegex(frappe.ValidationError, "locked to data source Site DB"):
            self.query(source="Arena").build()

    def test_unbound_queries_are_unchanged(self):
        self.query(source="Arena", locked=None)._validate_locked_data_source()

    def test_bound_query_cannot_change_to_builder(self):
        with self.assertRaises(frappe.ValidationError):
            self.query(native=False)._validate_locked_data_source()

    def test_extra_operations_cannot_bypass_binding(self):
        query = self.query()
        query.operations.append({"type": "sql", "data_source": "Arena", "raw_sql": "SELECT 2"})
        with self.assertRaises(frappe.ValidationError):
            query._validate_locked_data_source()
