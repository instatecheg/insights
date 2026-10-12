import csv
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

import frappe

from insights.api import translations


class TestInsightsLanguage(TestCase):
    def test_arabic_financial_catalog_is_well_formed(self):
        path = Path(frappe.get_app_path("insights", "translations", "ar.csv"))
        with path.open(encoding="utf-8", newline="") as catalog:
            rows = list(csv.reader(catalog))
        self.assertTrue(all(len(row) == 2 and all(row) for row in rows))
        self.assertEqual(len(rows), len({row[0] for row in rows}))
        messages = dict(rows)
        for label in (
            "Clear selections",
            "Done selecting",
            "Apply P&L Filters",
            "No data for these filters",
        ):
            self.assertIn(label, messages)
            self.assertNotEqual(messages[label], label)

    @patch.object(translations, "get_user_default", return_value="ar")
    def test_insights_preference_takes_priority(self, default):
        with patch.object(frappe.db, "get_value") as get_value:
            self.assertEqual(translations.get_language(), "ar")
            get_value.assert_not_called()

    @patch.object(translations, "get_user_default", return_value=None)
    def test_existing_user_language_is_fallback(self, default):
        with patch.object(frappe.db, "get_value", return_value="fr"):
            self.assertEqual(translations.get_language(), "fr")

    def test_guest_uses_site_language(self):
        with patch.object(frappe, "session", frappe._dict(user="Guest")):
            with patch.object(frappe.db, "get_single_value", return_value="ar"):
                self.assertEqual(translations.get_language(), "ar")
            with self.assertRaises(frappe.PermissionError):
                translations.set_language("ar")

    @patch.object(translations, "set_user_default")
    def test_switch_is_insights_only(self, set_default):
        with patch.object(frappe.db, "set_value") as set_value:
            self.assertEqual(translations.set_language("ar"), "ar")
            set_default.assert_called_once_with("insights_language", "ar")
            set_value.assert_not_called()

    @patch.object(translations, "set_user_default")
    def test_invalid_language_cannot_be_saved(self, set_default):
        with self.assertRaises(frappe.ValidationError):
            translations.set_language("invalid")
        set_default.assert_not_called()

    @patch.object(translations, "get_language", return_value="ar")
    @patch.object(translations, "get_all_translations", return_value={"Income": "الإيرادات"})
    def test_translation_dictionary_uses_effective_language(self, dictionary, language):
        self.assertEqual(translations.get_translations(), {"Income": "الإيرادات"})
        dictionary.assert_called_once_with("ar")
