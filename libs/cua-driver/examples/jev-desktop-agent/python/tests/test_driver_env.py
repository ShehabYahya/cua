from __future__ import annotations

import base64
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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


if __name__ == "__main__":
    unittest.main()
