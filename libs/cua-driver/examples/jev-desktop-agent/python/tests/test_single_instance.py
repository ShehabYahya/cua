from __future__ import annotations

import sys
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from PySide6.QtCore import QCoreApplication
    from single_instance import PorterSingleInstance
except ModuleNotFoundError as error:
    if error.name and error.name.startswith("PySide6"):
        raise unittest.SkipTest(
            "PySide6 is an optional native-GUI dependency"
        ) from error
    raise


class SingleInstanceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def test_second_launch_notifies_resident_instance(self):
        name = "porter-test-" + uuid.uuid4().hex
        first = PorterSingleInstance(name)
        second = PorterSingleInstance(name)
        activations = []
        first.activationRequested.connect(lambda: activations.append(True))

        try:
            self.assertTrue(first.acquire_or_notify())
            self.assertFalse(second.acquire_or_notify())

            for _ in range(10):
                self.app.processEvents()
                if activations:
                    break

            self.assertEqual(activations, [True])
        finally:
            second.close()
            first.close()


if __name__ == "__main__":
    unittest.main()
