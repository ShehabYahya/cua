from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from policy import classify_risk, field_is_sensitive


class PolicyTest(unittest.TestCase):
    def test_external_send_requires_confirmation(self):
        self.assertEqual(
            classify_risk('Activate button "Send".', tool="click"),
            "confirm",
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


if __name__ == "__main__":
    unittest.main()
