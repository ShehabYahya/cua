from __future__ import annotations

import asyncio
import base64
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contracts import DriverRefusal
from driver import CuaMcpDriver, driver_child_environment


class DriverEnvironmentTest(unittest.TestCase):
    def test_linux_defaults_to_is_enabled_only(self) -> None:
        with patch.object(sys, "platform", "linux"), patch.dict(os.environ, {}, clear=True):
            env = driver_child_environment()
        self.assertEqual(
            env["CUA_DRIVER_RS_A11Y_ADVERTISE_MODE"],
            "is_enabled_only",
        )
        self.assertNotIn("CUA_DRIVER_RS_ENABLE_WAYLAND", env)

    def test_wayland_session_enables_native_wayland_backend(self) -> None:
        with patch.object(sys, "platform", "linux"), patch.dict(
            os.environ,
            {"WAYLAND_DISPLAY": "wayland-0"},
            clear=True,
        ):
            env = driver_child_environment()
        self.assertEqual(env["CUA_DRIVER_RS_ENABLE_WAYLAND"], "1")
        self.assertEqual(
            env["CUA_DRIVER_RS_A11Y_ADVERTISE_MODE"],
            "is_enabled_only",
        )

    def test_explicit_overrides_are_preserved(self) -> None:
        with patch.object(sys, "platform", "linux"), patch.dict(
            os.environ,
            {
                "WAYLAND_DISPLAY": "wayland-0",
                "CUA_DRIVER_RS_A11Y_ADVERTISE_MODE": "none",
                "CUA_DRIVER_RS_ENABLE_WAYLAND": "0",
            },
            clear=True,
        ):
            env = driver_child_environment()
        self.assertEqual(env["CUA_DRIVER_RS_A11Y_ADVERTISE_MODE"], "none")
        self.assertEqual(env["CUA_DRIVER_RS_ENABLE_WAYLAND"], "0")


    def test_session_start_prefers_call_start_session(self) -> None:
        driver = CuaMcpDriver()
        driver._tool_schemas = {
            "call_start_session": {"properties": {}},
            "start_session": {"properties": {}},
        }
        calls = []

        async def fake_call(name, arguments):
            calls.append((name, arguments))
            return {}

        driver._call = fake_call
        asyncio.run(driver._start_driver_session(require=True))
        self.assertEqual(calls, [("call_start_session", {})])

    def test_session_start_falls_back_to_legacy_start_session(self) -> None:
        driver = CuaMcpDriver()
        driver._tool_schemas = {
            "start_session": {"properties": {}},
        }
        calls = []

        async def fake_call(name, arguments):
            calls.append((name, arguments))
            return {}

        driver._call = fake_call
        asyncio.run(driver._start_driver_session(require=True))
        self.assertEqual(calls, [("start_session", {})])

    def test_tool_invocation_failed_becomes_recoverable_driver_refusal(self) -> None:
        class FakeSession:
            async def call_tool(self, name, arguments):
                return SimpleNamespace(
                    structuredContent=None,
                    isError=True,
                    content=[
                        SimpleNamespace(
                            type="text",
                            text="tool_invocation_failed",
                        )
                    ],
                )

        driver = CuaMcpDriver()
        driver._session = FakeSession()
        driver._tool_schemas = {"click": {"properties": {}}}

        with self.assertRaises(DriverRefusal) as raised:
            asyncio.run(driver._call("click", {}))

        self.assertEqual(
            raised.exception.code,
            "tool_invocation_failed",
        )

    def test_inline_mcp_image_is_materialized_for_vision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            driver = CuaMcpDriver()
            driver._temp_dir = tmp
            payload = b"fake-png-bytes"
            path = driver._materialize_inline_image(
                {
                    "__inline_images": [
                        {
                            "data": base64.b64encode(payload).decode("ascii"),
                            "mime_type": "image/png",
                        }
                    ]
                },
                stem="window",
            )
            self.assertIsNotNone(path)
            self.assertEqual(Path(path).read_bytes(), payload)


    def test_missing_capture_binding_is_a_capability_limit_not_health_failure(self) -> None:
        driver = CuaMcpDriver()
        driver._tool_schemas = {
            "get_window_state": {"properties": {}},
            "click": {"properties": {}},
        }
        driver.capture_bound_click = False
        limitations = driver.capability_limitations()
        self.assertTrue(
            any("capture-bound raw pixel clicks" in item for item in limitations)
        )

if __name__ == "__main__":
    unittest.main()
