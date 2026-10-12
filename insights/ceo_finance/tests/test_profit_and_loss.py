import copy
from unittest import TestCase

from insights.ceo_finance.dashboard_setup import add_summary_filters


class TestSummaryFilters(TestCase):
    def test_summary_controls_share_filters_and_do_not_duplicate(self):
        items = [
            {
                "type": "filter",
                "filter_name": "Company",
                "values_query": "combined",
                "links": {"local": "`local`.`company_label`"},
                "layout": {"i": "company", "x": 0, "y": 0, "w": 6, "h": 3},
            },
            {
                "type": "filter",
                "filter_name": "Reporting Period",
                "finance_period": True,
                "default_operator": "within",
                "default_value": "Current Year",
                "links": {"local": "`local`.`posting_date`"},
                "layout": {"i": "period", "x": 6, "y": 0, "w": 14, "h": 3},
            },
            {
                "type": "text",
                "text": "<h3>Separate Company Summaries</h3>",
                "layout": {"i": "heading", "x": 0, "y": 20, "w": 20, "h": 2},
            },
            {"type": "chart", "chart": "local", "layout": {"i": "local", "x": 0, "y": 22, "w": 5, "h": 4}},
        ]
        add_summary_filters(items)
        self.assertEqual(items[3]["layout"]["y"], 25)
        self.assertEqual(items[-2]["filter_name"], "Company")
        self.assertEqual(items[-2]["values_query"], "combined")
        self.assertEqual(items[-2]["links"], items[0]["links"])
        self.assertEqual(items[-1]["filter_name"], "Reporting Period")
        self.assertTrue(items[-1]["finance_period"])
        self.assertEqual(items[-1]["layout"]["y"], 22)
        before = copy.deepcopy(items)
        add_summary_filters(items)
        self.assertEqual(items, before)
