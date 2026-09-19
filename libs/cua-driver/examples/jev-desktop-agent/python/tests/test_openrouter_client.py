from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from openrouter_client import CHAT_ENDPOINT, OpenRouterClient


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


class ClientTest(unittest.TestCase):
    def test_chat_json_uses_privacy_provider_policy(self):
        captured = {}

        def opener(request, timeout):
            captured["url"] = request.full_url
            captured["body"] = json.loads(request.data)
            return FakeResponse(
                {"choices": [{"message": {"content": "{\"ok\":true}"}}]}
            )

        client = OpenRouterClient("test", opener=opener)
        result = client.chat_json(system="s", prompt="p")
        self.assertEqual(result, {"ok": True})
        self.assertEqual(captured["url"], CHAT_ENDPOINT)
        self.assertTrue(captured["body"]["provider"]["zdr"])
        self.assertEqual(
            captured["body"]["provider"]["data_collection"],
            "deny",
        )


if __name__ == "__main__":
    unittest.main()
