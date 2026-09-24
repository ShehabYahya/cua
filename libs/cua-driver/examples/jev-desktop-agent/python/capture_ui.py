from __future__ import annotations

import argparse
import os
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEventLoop, QSettings, QTimer, QUrl
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication

from global_shortcuts import APP_ID
from porter_maintenance import PorterMaintenanceModel
from porter_qt import PorterRuntimeThread, PorterViewModel
from porter_settings import (
    AutostartManager,
    PorterAppSettings,
    PorterSettingsModel,
    PorterSettingsStore,
    SecretStore,
)


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
UI_DIR = ROOT / "ui"
ASSETS_DIR = ROOT / "assets"


class FakeKeyring:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, name: str):
        return self.values.get((service, name))

    def set_password(self, service: str, name: str, value: str) -> None:
        self.values[(service, name)] = value

    def delete_password(self, service: str, name: str) -> None:
        self.values.pop((service, name), None)


def _find_root(engine: QQmlApplicationEngine, name: str):
    for item in engine.rootObjects():
        if item.objectName() == name:
            return item
    raise RuntimeError(f"QML root {name!r} was not created")


def _save(window, path: Path) -> None:
    image = window.grabWindow()
    if image.isNull():
        raise RuntimeError(f"Could not capture {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not image.save(str(path)):
        raise RuntimeError(f"Could not save {path}")


def _settle_animations(milliseconds: int = 240) -> None:
    loop = QEventLoop()
    QTimer.singleShot(milliseconds, loop.quit)
    loop.exec()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Capture screenshots from Porter's real Qt/QML UI"
    )
    parser.add_argument("output", type=Path)
    parser.add_argument("--width", type=int, default=1180)
    parser.add_argument("--height", type=int, default=760)
    args = parser.parse_args()

    QCoreApplication.setOrganizationName("Porter")
    QCoreApplication.setOrganizationDomain("porter.local")
    QCoreApplication.setApplicationName("Porter")
    QGuiApplication.setDesktopFileName(APP_ID)
    QQuickStyle.setStyle("Basic")

    app = QApplication(sys.argv[:1])
    app.setApplicationDisplayName("Porter")
    app.setWindowIcon(QIcon(str(ASSETS_DIR / "porter-ring.svg")))

    temp = tempfile.TemporaryDirectory()
    settings_path = Path(temp.name) / "porter-preview.ini"
    store = PorterSettingsStore(
        QSettings(str(settings_path), QSettings.IniFormat)
    )
    preview = PorterAppSettings(
        provider="openrouter",
        hands_free=True,
        onboarding_complete=True,
        preferred_name="Shehab",
        start_at_login=True,
        accent_color="#49A7FF",
        compact_idle_opacity=0.22,
        compact_hover_opacity=0.76,
    )
    store.save(preview)

    keyring = FakeKeyring()
    keyring.set_password(
        SecretStore.SERVICE,
        SecretStore.OPENROUTER,
        "preview-only-key",
    )
    secrets = SecretStore(keyring)

    worker = PorterRuntimeThread(
        preview.runtime_config(),
        preview.voice_config(),
        preview.shortcut_config(),
        parent=app,
    )
    porter = PorterViewModel(worker, parent=app)
    porter._set_state("ready")
    porter._set_status("Ready")
    porter._set_detail("Porter is connected to your desktop")
    porter._set_listening(True)
    porter._shortcut_status = "Global shortcut ready — Ctrl+Alt+Space"
    porter.shortcutStatusChanged.emit()

    autostart = AutostartManager(
        ["/opt/porter/porter"],
        path=Path(temp.name) / "porter.desktop",
    )
    settings_model = PorterSettingsModel(
        worker,
        store=store,
        secrets=secrets,
        autostart=autostart,
        parent=app,
    )
    maintenance = PorterMaintenanceModel(parent=app)

    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("porter", porter)
    engine.rootContext().setContextProperty(
        "settingsModel",
        settings_model,
    )
    engine.rootContext().setContextProperty(
        "maintenance",
        maintenance,
    )
    engine.load(QUrl.fromLocalFile(str(UI_DIR / "Main.qml")))
    engine.load(QUrl.fromLocalFile(str(UI_DIR / "CompactBar.qml")))
    if len(engine.rootObjects()) < 2:
        return 2

    main_window = _find_root(engine, "mainWindow")
    compact_window = _find_root(engine, "compactWindow")
    main_window.setWidth(max(main_window.minimumWidth(), args.width))
    main_window.setHeight(max(main_window.minimumHeight(), args.height))
    main_window.show()

    output = args.output.resolve()

    steps: list[tuple[str, callable]] = []

    def page(index: int, filename: str) -> None:
        def capture() -> None:
            main_window.setProperty("currentPage", index)
            app.processEvents()
            _settle_animations()
            _save(main_window, output / filename)

        steps.append((filename, capture))

    page(0, "01-home.png")
    page(1, "02-voice-audio.png")
    page(2, "03-models-providers.png")
    page(3, "04-computer-control.png")
    page(4, "05-shortcuts.png")
    page(5, "06-personalization.png")
    page(6, "07-appearance.png")

    def diagnostics() -> None:
        porter._diagnostics_status = "Healthy"
        porter._diagnostics_summary = (
            "4 visible window(s), 18 known app(s), "
            "0 health warning(s), 1 limitation(s)."
        )
        porter._diagnostics_details = (
            "Limitations:\n"
            "• Driver does not advertise capture-bound raw pixel clicks.\n\n"
            "Capabilities:\n"
            "{\n"
            '  "native_observation": {"list_windows": true, '
            '"get_window_state": true},\n'
            '  "input": {"coordinate_click_supported": true},\n'
            '  "perception": {"parse_visual_regions": true}\n'
            "}\n\n"
            "Provider: openrouter\n"
            "Jev model: ~typesafe/jev-latest\n"
            "Visual clicks: strict"
        )
        porter.diagnosticsChanged.emit()
        main_window.setProperty("currentPage", 7)
        app.processEvents()
        _settle_animations()
        _save(main_window, output / "08-advanced-diagnostics.png")

    steps.append(("08-advanced-diagnostics.png", diagnostics))

    def about_updates() -> None:
        main_window.setProperty("currentPage", 8)
        app.processEvents()
        _settle_animations()
        _save(main_window, output / "09-about-updates.png")

    steps.append(("09-about-updates.png", about_updates))

    def compact_idle() -> None:
        compact_window.show()
        compact_window.setWidth(680)
        compact_window.setHeight(104)
        porter._set_state("ready")
        porter._set_status("Ready")
        porter._set_detail("Porter is connected to your desktop")
        app.processEvents()
        _settle_animations()
        _save(compact_window, output / "10-compact-bar.png")
        compact_window.hide()

    steps.append(("10-compact-bar.png", compact_idle))

    def compact_listening() -> None:
        compact_window.show()
        porter._set_busy(False)
        porter._set_listening(True)
        porter._set_state("listening")
        porter._set_status("Listening…")
        porter._set_detail("Keep speaking")
        app.processEvents()
        _settle_animations()
        _save(compact_window, output / "12-compact-listening.png")
        compact_window.hide()

    steps.append(("12-compact-listening.png", compact_listening))

    def compact_working() -> None:
        compact_window.show()
        porter._set_listening(False)
        porter._set_busy(True)
        porter._set_state("working")
        porter._set_status("Opening Settings…")
        porter._set_detail("Finding the system application")
        app.processEvents()
        _settle_animations()
        _save(compact_window, output / "13-compact-working.png")
        compact_window.hide()

    steps.append(("13-compact-working.png", compact_working))

    def compact_error() -> None:
        compact_window.show()
        porter._set_busy(False)
        porter._set_state("error")
        porter._set_status("Failed")
        porter._set_detail("Settings could not be opened")
        app.processEvents()
        _settle_animations()
        _save(compact_window, output / "14-compact-error.png")
        compact_window.hide()

    steps.append(("14-compact-error.png", compact_error))

    def compact_cancelling() -> None:
        compact_window.show()
        porter._set_listening(False)
        porter._set_manual_voice_active(False)
        porter._set_voice_input_state("idle")
        porter._set_busy(True)
        porter._set_cancelling(True)
        porter._set_state("working")
        porter._set_status("Stopping…")
        porter._set_detail("Cancelling the current Porter task")
        app.processEvents()
        _settle_animations()
        _save(compact_window, output / "15-compact-cancelling.png")
        compact_window.hide()

    steps.append(("15-compact-cancelling.png", compact_cancelling))

    def compact_one_shot() -> None:
        compact_window.show()
        porter._set_busy(False)
        porter._set_cancelling(False)
        porter._set_listening(False)
        porter._set_manual_voice_active(True)
        porter._set_voice_input_state("speech")
        porter._set_state("listening")
        porter._set_status("Listening…")
        porter._set_detail("Speak one command")
        app.processEvents()
        _settle_animations()
        _save(compact_window, output / "16-compact-one-shot-listening.png")
        compact_window.hide()

    steps.append(("16-compact-one-shot-listening.png", compact_one_shot))

    def home_working_stop() -> None:
        porter._set_manual_voice_active(False)
        porter._set_voice_input_state("idle")
        porter._set_cancelling(False)
        porter._set_busy(True)
        porter._set_state("working")
        porter._set_status("Opening Settings…")
        porter._set_detail("Finding the system application")
        main_window.setProperty("currentPage", 0)
        app.processEvents()
        _settle_animations()
        _save(main_window, output / "17-home-working-stop.png")

    steps.append(("17-home-working-stop.png", home_working_stop))

    def home_one_shot() -> None:
        porter._set_busy(False)
        porter._set_cancelling(False)
        porter._set_listening(False)
        porter._set_manual_voice_active(True)
        porter._set_voice_input_state("speech")
        porter._set_state("listening")
        porter._set_status("Listening…")
        porter._set_detail("Speak one command")
        main_window.setProperty("currentPage", 0)
        app.processEvents()
        _settle_animations()
        _save(main_window, output / "18-home-one-shot-listening.png")

    steps.append(("18-home-one-shot-listening.png", home_one_shot))

    def voice_one_shot() -> None:
        porter._set_busy(False)
        porter._set_cancelling(False)
        porter._set_listening(False)
        porter._set_manual_voice_active(True)
        porter._set_voice_input_state("speech")
        porter._set_state("listening")
        porter._set_status("Listening…")
        porter._set_detail("Speak one command")
        main_window.setProperty("currentPage", 1)
        app.processEvents()
        _settle_animations()
        _save(main_window, output / "19-voice-one-shot.png")

    steps.append(("19-voice-one-shot.png", voice_one_shot))

    def onboarding() -> None:
        keyring.delete_password(
            SecretStore.SERVICE,
            SecretStore.OPENROUTER,
        )
        settings_model._saved = replace(
            settings_model._saved,
            onboarding_complete=False,
        )
        settings_model._draft = settings_model._saved
        settings_model.credentialsChanged.emit()
        settings_model.settingsChanged.emit()
        porter._set_state("ready")
        porter._set_status("Ready")
        porter._set_detail("Porter is connected to your desktop")
        main_window.setProperty("currentPage", 0)
        app.processEvents()
        _settle_animations()
        _save(main_window, output / "11-first-run-onboarding.png")

    steps.append(("11-first-run-onboarding.png", onboarding))

    index = 0

    def run_next() -> None:
        nonlocal index
        if index >= len(steps):
            app.quit()
            return
        _, action = steps[index]
        index += 1
        action()
        QTimer.singleShot(180, run_next)

    QTimer.singleShot(500, run_next)
    code = app.exec()

    _ = (engine, settings_model, porter, worker, maintenance)
    temp.cleanup()
    return int(code)


if __name__ == "__main__":
    raise SystemExit(main())
