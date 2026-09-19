from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from policy import classify_risk, field_is_sensitive, sensitive_text_intent


class PolicyTest(unittest.TestCase):
    def test_external_send_requires_confirmation(self):
        self.assertEqual(
            classify_risk('Activate button "Send".', tool="click"),
            "confirm",
        )

    def test_submit_search_is_not_consequential(self):
        self.assertEqual(
            classify_risk(
                'Activate list item "Alan Turing — Search with Google".',
                tool="click",
            ),
            "safe",
        )

    def test_password_field_is_denied(self):
        self.assertTrue(field_is_sensitive("Account password"))
        self.assertEqual(
            classify_risk(
                "Put prepared text into Password",
                tool="type_text",
                field_label="Password",
            ),
            "deny",
        )

    def test_pin_rule_does_not_match_shipping(self):
        self.assertFalse(field_is_sensitive("Shipping address"))

    def test_sensitive_text_intent_requires_entry_verb(self):
        self.assertTrue(sensitive_text_intent("type my password"))
        self.assertFalse(sensitive_text_intent("open my password manager"))


if __name__ == "__main__":
    unittest.main()
