from datetime import date
from unittest import TestCase
from unittest.mock import patch

import frappe
import ibis

from insights.ceo_finance import detailed_profit_and_loss as pl


class TestDetailedProfitAndLoss(TestCase):
    def setUp(self):
        self.periods = [
            {"key": "jan", "from_date": date(2026, 1, 1), "to_date": date(2026, 1, 31)},
            {"key": "feb", "from_date": date(2026, 2, 1), "to_date": date(2026, 2, 28)},
        ]
        self.accounts = [
            {"name": "Income", "root_type": "Income", "parent_account": "", "is_group": 1, "lft": 1},
            {"name": "Sales", "root_type": "Income", "parent_account": "Income", "is_group": 0, "lft": 2},
            {"name": "Expenses", "root_type": "Expense", "parent_account": "", "is_group": 1, "lft": 4},
            {"name": "Rent", "root_type": "Expense", "parent_account": "Expenses", "is_group": 0, "lft": 5},
        ]
        self.daily = [
            {"account": "Sales", "posting_date": date(2026, 1, 15), "credit": "100.01", "debit": 0},
            {"account": "Sales", "posting_date": date(2026, 2, 15), "credit": 200, "debit": 0},
            {"account": "Rent", "posting_date": date(2026, 1, 16), "credit": 0, "debit": 40},
        ]

    def test_account_hierarchy_and_decimal_amounts(self):
        rows = {row["account"]: row for row in pl.account_rows(self.accounts, self.daily, self.periods)}
        self.assertEqual(rows["Income"]["jan"], 100.01)
        self.assertEqual(rows["Sales"]["jan"], 100.01)
        self.assertEqual(rows["Income"]["total"], 300.01)
        self.assertEqual(rows["Expenses"]["jan"], 40)
        self.assertEqual(rows["Sales"]["indent"], 1)
        self.assertEqual(rows["Sales"]["parent_account"], "Income")
        self.assertEqual(rows["Income"]["parent_account"], "")

    def test_ceo_company_summary_uses_roots_without_double_counting(self):
        rows = pl.account_rows(self.accounts, self.daily, self.periods)
        summary = pl.company_summary(rows, "Source | C", "source", "C")
        self.assertEqual(summary["income"], 300.01)
        self.assertEqual(summary["expense"], 40)
        self.assertAlmostEqual(summary["profit"], 260.01)
        self.assertAlmostEqual(summary["margin"], 260.01 / 300.01 * 100)
        self.assertEqual(summary["source"], "source")

    def test_ceo_company_summary_accumulation_and_zero_revenue(self):
        rows = pl.account_rows(self.accounts, self.daily, self.periods, True)
        self.assertEqual(pl.company_summary(rows, "C", "s", "C")["income"], 300.01)
        rows = pl.account_rows(
            self.accounts,
            [{"account": "Rent", "posting_date": date(2026, 1, 1), "debit": 40, "credit": 0}],
            self.periods,
        )
        summary = pl.company_summary(rows, "C", "s", "C")
        self.assertEqual(summary["profit"], -40)
        self.assertIsNone(summary["margin"])

    def test_zero_parent_is_retained_for_nonzero_offsetting_children(self):
        accounts = [
            *self.accounts,
            {"name": "Refund", "root_type": "Income", "parent_account": "Income", "is_group": 0, "lft": 3},
        ]
        daily = [
            {"account": "Sales", "posting_date": date(2026, 1, 1), "debit": 0, "credit": 100},
            {"account": "Refund", "posting_date": date(2026, 1, 1), "debit": 100, "credit": 0},
        ]
        rows = pl.account_rows(accounts, daily, self.periods)
        self.assertEqual(pl.visible_accounts(rows, self.periods), {"Income", "Sales", "Refund"})

    def test_company_chart_period_values_and_accumulation(self):
        rows = pl.account_rows(self.accounts, self.daily, self.periods)
        chart = pl.company_period_chart(rows, self.periods)
        self.assertEqual(chart["income"], [100.01, 200])
        self.assertEqual(chart["expense"], [40, 0])
        self.assertAlmostEqual(chart["profit"][0], 60.01)
        self.assertEqual(chart["profit"][1], 200)
        rows = pl.account_rows(self.accounts, self.daily, self.periods, True)
        chart = pl.company_period_chart(rows, self.periods)
        self.assertEqual(chart["income"], [100.01, 300.01])
        self.assertEqual(chart["expense"], [40, 40])

    def test_accumulated_summary_uses_last_period_not_sum(self):
        rows = {row["account"]: row for row in pl.account_rows(self.accounts, self.daily, self.periods, True)}
        self.assertEqual(rows["Income"]["feb"], 300.01)
        self.assertEqual(rows["Income"]["total"], 300.01)

    def test_accumulation_resets_on_source_fiscal_year(self):
        years = [
            {"year_start_date": date(2025, 2, 1), "year_end_date": date(2026, 1, 31)},
            {"year_start_date": date(2026, 2, 1), "year_end_date": date(2027, 1, 31)},
        ]
        rows = {
            row["account"]: row
            for row in pl.account_rows(self.accounts, self.daily, self.periods, True, years)
        }
        self.assertEqual(rows["Income"]["feb"], 200)

    def test_expense_credit_is_a_refund_not_an_expense(self):
        daily = [{"account": "Rent", "posting_date": date(2026, 1, 15), "credit": 10, "debit": 0}]
        rows = {row["account"]: row for row in pl.account_rows(self.accounts, daily, self.periods)}
        self.assertEqual(rows["Expenses"]["jan"], -10)

    def test_cycle_is_rejected(self):
        self.accounts[0]["parent_account"] = "Sales"
        with self.assertRaises(frappe.ValidationError):
            pl.account_rows(self.accounts, self.daily, self.periods)

    def test_margin_uses_company_income_and_undefined_zero_revenue(self):
        rows = [{"jan": 20, "feb": 10, "total": 30}]
        result = pl.apply_view(rows, self.periods, "Margin", {"jan": 100, "feb": 0, "total": 100})
        self.assertEqual(result[0]["jan"], 20)
        self.assertIsNone(result[0]["feb"])
        self.assertEqual(rows[0]["feb"], 10)
        self.assertEqual(
            pl.apply_view([{"jan": 0, "feb": 0, "total": 0}], self.periods, "Margin", {})[0]["jan"], 0
        )

    @patch.object(pl, "table")
    def test_finance_book_defaults_and_explicit_book_follow_erpnext(self, table):
        gl = ibis.memtable(
            [
                {
                    "company": "C",
                    "is_cancelled": 0,
                    "voucher_type": "Journal Entry",
                    "is_opening": "No",
                    "posting_date": date(2020, 1, 1),
                    "finance_book": book,
                }
                for book in ("", "default", "other")
            ]
        )
        table.return_value = gl
        source = frappe._dict(name="source")
        record = frappe._dict(source_label="Source")
        company = {"name": "C", "default_finance_book": "default"}
        filters = frappe._dict(start=date(2020, 1, 1), end=date(2020, 1, 31))
        result = pl.filtered_gl(record, source, company, filters).execute()
        self.assertEqual(set(result["finance_book"]), {"", "default"})
        filters.finance_books = [pl.option_id("source", "other")]
        with self.assertRaises(frappe.ValidationError):
            pl.filtered_gl(record, source, company, filters)
        filters.include_default_book_entries = False
        result = pl.filtered_gl(record, source, company, filters).execute()
        self.assertEqual(set(result["finance_book"]), {"", "other"})

    @patch.object(pl, "table")
    def test_selected_book_on_another_source_does_not_match(self, table):
        table.return_value = ibis.memtable(
            [
                {
                    "company": "C",
                    "is_cancelled": 0,
                    "voucher_type": "Journal Entry",
                    "is_opening": "No",
                    "posting_date": date(2020, 1, 1),
                    "finance_book": "",
                }
            ]
        )
        filters = frappe._dict(
            start=date(2020, 1, 1),
            end=date(2020, 1, 31),
            finance_books=[pl.option_id("another", "same-book")],
        )
        result = pl.filtered_gl(
            frappe._dict(source_label="Source"), frappe._dict(name="source"), {"name": "C"}, filters
        )
        self.assertTrue(result.execute().empty)

    def test_growth_reuses_original_erpnext_rules(self):
        rows = [{"jan": 100, "feb": 150, "total": 250}]
        result = pl.apply_view(rows, self.periods, "Growth", {})
        self.assertEqual(result[0]["jan"], 100)
        self.assertEqual(result[0]["feb"], 50)
        self.assertEqual(rows[0]["feb"], 150)

    def test_tree_filter_includes_descendants(self):
        rows = [{"name": "root"}, {"name": "child", "parent": "root"}, {"name": "leaf", "parent": "child"}]
        self.assertEqual(pl.expand_tree(rows, ["root"], "parent"), ["child", "leaf", "root"])

    def test_source_qualification_prevents_name_collision(self):
        values = [pl.option_id("arena", "Shared"), pl.option_id("local", "Shared")]
        self.assertEqual(pl.selected(values, "local"), ["Shared"])
        self.assertEqual(pl.selected(values, "mos"), [])

    def test_company_inclusion_and_exclusion(self):
        state = {"operator": "in", "value": ["Arena | Arena Build"]}
        self.assertTrue(pl.company_matches("Arena | Arena Build", state))
        self.assertFalse(pl.company_matches("Mos Star | Moos Star", state))
        state["operator"] = "not in"
        self.assertFalse(pl.company_matches("Arena | Arena Build", state))
        self.assertTrue(pl.company_matches("Mos Star | Moos Star", state))

    def test_empty_company_selection_means_all_companies(self):
        for value in ([], None, ""):
            self.assertTrue(pl.company_matches("Mos Star | Moos Star", {"operator": "in", "value": value}))
            self.assertTrue(pl.company_matches("Arena | Arena Build", {"operator": "not in", "value": value}))

    def test_invalid_dates_periodicity_and_view_are_explicit(self):
        for values in [
            {"start": "2026-02-01", "end": "2026-01-01"},
            {"start": "2026-01-01", "end": "2026-12-31", "periodicity": "Weekly"},
            {"start": "2026-01-01", "end": "2026-12-31", "view": "Unsupported"},
        ]:
            with self.subTest(values=values), self.assertRaises(frappe.ValidationError):
                pl.validate_filters(values)

    @patch.object(pl.frappe.db, "get_single_value", return_value="ceo")
    def test_unconfigured_dashboard_cannot_read_sources(self, config):
        with self.assertRaises(frappe.PermissionError):
            pl.sources("another-dashboard")

    @patch.object(pl.frappe, "get_doc")
    @patch.object(pl.frappe.db, "get_single_value", return_value="ceo")
    def test_dashboard_read_access_is_required(self, config, get_doc):
        get_doc.return_value.check_permission.side_effect = frappe.PermissionError
        with self.assertRaises(frappe.PermissionError):
            pl.sources("ceo")
        get_doc.return_value.check_permission.assert_called_once_with("read")

    @patch.object(pl, "get_options")
    def test_unknown_source_filter_does_not_silently_return_data(self, options):
        options.return_value = {"finance_books": [], "cost_centers": [], "projects": [], "dimensions": []}
        with self.assertRaises(frappe.ValidationError):
            pl.validate_options("ceo", {"cost_centers": [pl.option_id("fake-source", "center")]})

    def test_original_erpnext_report_is_not_overridden(self):
        from insights import hooks

        self.assertFalse(
            getattr(hooks, "override_whitelisted_methods", {}).get("frappe.desk.query_report.get_script")
        )

    def test_future_company_cards_stay_above_detailed_report(self):
        from insights.ceo_finance.api import position_detailed_report

        items = [
            {"type": "chart", "layout": {"y": 20, "h": 7}},
            {"type": "financial_report", "layout": {"y": 28, "h": 28}},
            {"type": "chart", "layout": {"y": 35, "h": 7}},
        ]
        position_detailed_report(items)
        self.assertEqual(items[1]["layout"]["y"], 43)
        position_detailed_report(items)
        self.assertEqual(items[1]["layout"]["y"], 43)
