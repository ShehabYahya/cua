from __future__ import annotations

import os
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from PySide6.QtCore import QObject, QSettings, Signal
except ModuleNotFoundError as error:
    raise unittest.SkipTest(
        "PySide6 is an optional native-GUI dependency"
    ) from error

from porter_settings import (
    AutostartManager,
    PorterAppSettings,
    PorterSettingsModel,
    PorterSettingsStore,
    SecretStore,
)


class FakeKeyring:
    def __init__(self) -> None:
        self.values = {}

    def get_password(self, service, name):
        return self.values.get((service, name))

    def set_password(self, service, name, value):
        self.values[(service, name)] = value

    def delete_password(self, service, name):
        self.values.pop((service, name), None)


class ImmediateWorker(QObject):
    reconfigured = Signal()
    reconfigureFailed = Signal(str)

    def __init__(self):
        super().__init__()
        self.calls = []

    def reconfigure(self, runtime, voice, shortcut):
        self.calls.append((runtime, voice, shortcut))
        # Deliberately emit before returning to catch the historical apply race.
        self.reconfigured.emit()
        return object()


class DelayedWorker(QObject):
    reconfigured = Signal()
    reconfigureFailed = Signal(str)

    def __init__(self):
        super().__init__()
        self.calls = []

    def reconfigure(self, runtime, voice, shortcut):
        self.calls.append((runtime, voice, shortcut))
        return object()


class PorterSettingsTest(unittest.TestCase):
    def test_corrupt_numeric_and_color_settings_fall_back_safely(self):
        with tempfile.TemporaryDirectory() as tmp:
            qsettings = QSettings(
                str(Path(tmp) / "porter.ini"),
                QSettings.IniFormat,
            )
            qsettings.setValue("voice/silence", "not-a-number")
            qsettings.setValue("appearance/accent_color", "not-a-color")
            qsettings.setValue("appearance/compact_idle_opacity", -5)
            qsettings.setValue("appearance/compact_hover_opacity", "nan")
            qsettings.setValue("advanced/max_steps", 9999)
            qsettings.setValue("advanced/max_candidates", "broken")

            actual = PorterSettingsStore(qsettings).load()

            self.assertAlmostEqual(actual.voice_silence, 0.55)
            self.assertEqual(actual.accent_color, "#49A7FF")
            self.assertAlmostEqual(actual.compact_idle_opacity, 0.08)
            self.assertAlmostEqual(actual.compact_hover_opacity, 0.72)
            self.assertEqual(actual.max_steps, 200)
            self.assertEqual(actual.max_candidates, 32)

    def test_qsettings_round_trip_keeps_runtime_choices(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "porter.ini"
            qsettings = QSettings(str(path), QSettings.IniFormat)
            store = PorterSettingsStore(qsettings)

            expected = replace(
                PorterAppSettings(),
                provider="openrouter",
                jev_model="~typesafe/jev-latest",
                vision_enabled=False,
                voice_silence=0.7,
                hands_free=False,
                download_root="/tmp/downloads",
                confirm_actions=True,
                allow_foreground=False,
                visual_click_mode="permissive",
                start_at_login=True,
                global_shortcut_enabled=False,
                global_shortcut_trigger="CTRL+SHIFT+space",
                preferred_name="Shehab",
                accent_color="#6B7CFF",
                compact_idle_opacity=0.20,
                compact_hover_opacity=0.80,
                animations_enabled=False,
            )
            store.save(expected)
            actual = store.load()

            self.assertEqual(actual.provider, "openrouter")
            self.assertFalse(actual.vision_enabled)
            self.assertAlmostEqual(actual.voice_silence, 0.7)
            self.assertFalse(actual.hands_free)
            self.assertEqual(actual.download_root, "/tmp/downloads")
            self.assertTrue(actual.confirm_actions)
            self.assertFalse(actual.allow_foreground)
            self.assertEqual(actual.visual_click_mode, "permissive")
            self.assertTrue(actual.start_at_login)
            self.assertFalse(actual.global_shortcut_enabled)
            self.assertEqual(
                actual.global_shortcut_trigger,
                "CTRL+SHIFT+space",
            )
            self.assertEqual(actual.preferred_name, "Shehab")
            self.assertEqual(actual.accent_color, "#6B7CFF")
            self.assertAlmostEqual(actual.compact_idle_opacity, 0.20)
            self.assertAlmostEqual(actual.compact_hover_opacity, 0.80)
            self.assertFalse(actual.animations_enabled)

            shortcut = actual.shortcut_config()
            self.assertFalse(shortcut.enabled)
            self.assertEqual(
                shortcut.preferred_trigger,
                "CTRL+SHIFT+space",
            )

            runtime = actual.runtime_config()
            self.assertEqual(runtime.visual_click_mode, "permissive")
            self.assertFalse(runtime.allow_foreground)
            self.assertTrue(runtime.enforce_policy)

    def test_credentials_use_keyring_not_qsettings(self):
        with tempfile.TemporaryDirectory() as tmp:
            qpath = Path(tmp) / "porter.ini"
            qsettings = QSettings(str(qpath), QSettings.IniFormat)
            PorterSettingsStore(qsettings).save(PorterAppSettings())

            backend = FakeKeyring()
            secrets = SecretStore(backend)
            secrets.set(SecretStore.OPENROUTER, "secret-openrouter")

            self.assertEqual(
                secrets.get(SecretStore.OPENROUTER),
                "secret-openrouter",
            )
            self.assertNotIn(
                "secret-openrouter",
                qpath.read_text(encoding="utf-8"),
            )

            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop(SecretStore.OPENROUTER, None)
                secrets.apply_to_environment()
                self.assertEqual(
                    os.environ.get(SecretStore.OPENROUTER),
                    "secret-openrouter",
                )

    def test_apply_handles_immediate_reconfigure_signal(self):
        with tempfile.TemporaryDirectory() as tmp:
            qpath = Path(tmp) / "porter.ini"
            store = PorterSettingsStore(
                QSettings(str(qpath), QSettings.IniFormat)
            )
            store.save(PorterAppSettings())

            worker = ImmediateWorker()
            model = PorterSettingsModel(
                worker,
                store=store,
                secrets=SecretStore(FakeKeyring()),
                autostart=AutostartManager(
                    ["/opt/porter/porter"],
                    path=Path(tmp) / "autostart.desktop",
                ),
            )

            model.setVisualClickMode("permissive")
            self.assertTrue(model.dirty)
            model.apply()

            self.assertFalse(model.dirty)
            self.assertEqual(
                store.load().visual_click_mode,
                "permissive",
            )
            self.assertEqual(model.applyStatus, "Settings applied.")

    def test_apply_freezes_snapshot_and_preserves_newer_edits(self):
        with tempfile.TemporaryDirectory() as tmp:
            qpath = Path(tmp) / "porter.ini"
            store = PorterSettingsStore(
                QSettings(str(qpath), QSettings.IniFormat)
            )
            store.save(PorterAppSettings())
            worker = DelayedWorker()
            model = PorterSettingsModel(
                worker,
                store=store,
                secrets=SecretStore(FakeKeyring()),
                autostart=AutostartManager(
                    ["/opt/porter/porter"],
                    path=Path(tmp) / "autostart.desktop",
                ),
            )

            model.setVisualClickMode("permissive")
            model.apply()
            self.assertTrue(model.applying)

            model.setMaxSteps(99)
            worker.reconfigured.emit()

            saved = store.load()
            self.assertFalse(model.applying)
            self.assertEqual(saved.visual_click_mode, "permissive")
            self.assertEqual(saved.max_steps, 30)
            self.assertEqual(model.maxSteps, 99)
            self.assertTrue(model.dirty)
            self.assertEqual(
                model.applyStatus,
                "Settings applied. Newer edits are still unsaved.",
            )

    def test_failed_apply_keeps_draft_dirty(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = PorterSettingsStore(
                QSettings(str(Path(tmp) / "porter.ini"), QSettings.IniFormat)
            )
            store.save(PorterAppSettings())
            worker = DelayedWorker()
            model = PorterSettingsModel(
                worker,
                store=store,
                secrets=SecretStore(FakeKeyring()),
                autostart=AutostartManager(
                    ["/opt/porter/porter"],
                    path=Path(tmp) / "autostart.desktop",
                ),
            )

            model.setProvider("openrouter")
            model.apply()
            worker.reconfigureFailed.emit("provider unavailable")

            self.assertFalse(model.applying)
            self.assertTrue(model.dirty)
            self.assertEqual(store.load().provider, "auto")
            self.assertIn("provider unavailable", model.applyStatus)

    def test_finished_onboarding_stays_finished_without_live_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            qpath = Path(tmp) / "porter.ini"
            store = PorterSettingsStore(
                QSettings(str(qpath), QSettings.IniFormat)
            )
            store.save(PorterAppSettings())

            worker = ImmediateWorker()
            keyring = FakeKeyring()
            autostart = AutostartManager(
                ["/opt/porter/porter"],
                path=Path(tmp) / "autostart.desktop",
            )
            model = PorterSettingsModel(
                worker,
                store=store,
                secrets=SecretStore(keyring),
                autostart=autostart,
            )
            self.assertTrue(model.needsOnboarding)

            model.setOnboardingComplete(True)
            self.assertFalse(model.needsOnboarding)

            restored = PorterSettingsModel(
                ImmediateWorker(),
                store=PorterSettingsStore(
                    QSettings(str(qpath), QSettings.IniFormat)
                ),
                secrets=SecretStore(FakeKeyring()),
                autostart=autostart,
            )
            self.assertTrue(restored.onboardingComplete)
            self.assertFalse(restored.needsOnboarding)

    def test_autostart_file_is_created_and_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "porter.desktop"
            manager = AutostartManager(
                ["/opt/Porter/porter", "--resident"],
                path=path,
            )
            manager.set_enabled(True)
            content = path.read_text(encoding="utf-8")
            self.assertIn("[Desktop Entry]", content)
            self.assertIn('Exec="/opt/Porter/porter" "--resident"', content)
            self.assertTrue(manager.enabled())

            manager.set_enabled(False)
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
