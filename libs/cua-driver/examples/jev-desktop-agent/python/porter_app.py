from __future__ import annotations

import argparse
import os
import sys
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QTimer, QUrl
from PySide6.QtGui import QAction, QGuiApplication, QIcon
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from porter_maintenance import PorterMaintenanceModel
from version import APP_ID, __version__
from openrouter_client import (
    DEFAULT_REASONING_MODEL,
    DEFAULT_STT_MODEL,
)
from porter_qt import PorterRuntimeThread, PorterViewModel
from porter_settings import (
    AutostartManager,
    PorterSettingsModel,
    PorterSettingsStore,
    SecretStore,
)


HERE = Path(__file__).resolve().parent
ROOT = (
    HERE.parent
    if (HERE.parent / "ui").is_dir()
    else HERE
)
UI_DIR = ROOT / "ui"
ASSETS_DIR = ROOT / "assets"


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Porter native desktop app")
    result.add_argument(
        "--version",
        action="version",
        version=f"Porter {__version__}",
    )
    result.add_argument(
        "--smoke-test",
        action="store_true",
        help="load both native QML windows and exit without starting the backend",
    )
    result.add_argument(
        "--provider",
        choices=["auto", "openrouter", "typesafe"],
        default=os.getenv("PORTER_PROVIDER", "auto"),
    )
    result.add_argument(
        "--no-vision",
        action="store_true",
        default=os.getenv("PORTER_NO_VISION", "").casefold()
        in {"1", "true", "yes"},
    )
    result.add_argument(
        "--vision-model",
        default=os.getenv(
            "PORTER_VISION_MODEL",
            os.getenv("JEV_DESKTOP_VISION_MODEL", DEFAULT_REASONING_MODEL),
        ),
    )
    result.add_argument(
        "--writer-model",
        default=os.getenv(
            "PORTER_WRITER_MODEL",
            os.getenv("JEV_DESKTOP_WRITER_MODEL", DEFAULT_REASONING_MODEL),
        ),
    )
    result.add_argument(
        "--download-root",
        default=os.getenv(
            "PORTER_DOWNLOAD_ROOT",
            os.getenv("JEV_DESKTOP_DOWNLOAD_ROOT"),
        ),
    )
    result.add_argument(
        "--confirm-actions",
        action="store_true",
        default=os.getenv("PORTER_CONFIRM_ACTIONS", "").casefold()
        in {"1", "true", "yes"},
    )
    result.add_argument(
        "--no-hands-free",
        action="store_true",
        default=os.getenv("PORTER_HANDS_FREE", "1").casefold()
        in {"0", "false", "no", "off"},
        help="start Porter with automatic microphone listening disabled",
    )
    result.add_argument(
        "--voice-silence",
        type=float,
        default=float(os.getenv("PORTER_VOICE_SILENCE", "0.55")),
        help="trailing silence in seconds that ends a spoken command",
    )
    result.add_argument(
        "--voice-language",
        default=os.getenv("PORTER_VOICE_LANGUAGE"),
        help="optional ISO-639-1 STT language hint",
    )
    result.add_argument(
        "--stt-model",
        default=os.getenv("PORTER_STT_MODEL", DEFAULT_STT_MODEL),
    )
    result.add_argument(
        "--mic",
        type=int,
        default=(
            int(os.environ["PORTER_MIC"])
            if os.getenv("PORTER_MIC", "").strip()
            else None
        ),
        help="sounddevice microphone index",
    )
    return result


def _explicit_option(name: str) -> bool:
    prefix = name + "="
    return any(
        argument == name or argument.startswith(prefix)
        for argument in sys.argv[1:]
    )


def _find_root(engine: QQmlApplicationEngine, name: str):
    for item in engine.rootObjects():
        if item.objectName() == name:
            return item
    raise RuntimeError(f"QML root {name!r} was not created")


def _show_window(window) -> None:
    window.show()
    try:
        window.raise_()
    except Exception:
        pass
    try:
        window.requestActivate()
    except Exception:
        pass


def _toggle_window(window) -> None:
    if window.isVisible():
        window.hide()
    else:
        _show_window(window)


def main() -> int:
    args = parser().parse_args()

    QCoreApplication.setOrganizationName("Porter")
    QCoreApplication.setOrganizationDomain("porter.local")
    QCoreApplication.setApplicationName("Porter")
    QCoreApplication.setApplicationVersion(__version__)
    QGuiApplication.setDesktopFileName(APP_ID)
    QQuickStyle.setStyle("Basic")

    app = QApplication(sys.argv[:1])
    app.setApplicationDisplayName("Porter")
    app.setApplicationName("Porter")

    icon = QIcon(str(ASSETS_DIR / "porter-ring.svg"))
    app.setWindowIcon(icon)

    settings_store = PorterSettingsStore()
    secret_store = SecretStore()
    secret_store.apply_to_environment()
    initial = settings_store.load()

    # Native settings are authoritative after the first save, while explicit
    # launch flags remain useful for development and one-off overrides.
    overrides = {}
    if _explicit_option("--provider") or "PORTER_PROVIDER" in os.environ:
        overrides["provider"] = args.provider
    if _explicit_option("--no-vision") or "PORTER_NO_VISION" in os.environ:
        overrides["vision_enabled"] = not args.no_vision
    if _explicit_option("--vision-model") or "PORTER_VISION_MODEL" in os.environ:
        overrides["vision_model"] = args.vision_model
    if _explicit_option("--writer-model") or "PORTER_WRITER_MODEL" in os.environ:
        overrides["writer_model"] = args.writer_model
    if _explicit_option("--download-root") or "PORTER_DOWNLOAD_ROOT" in os.environ:
        overrides["download_root"] = args.download_root or ""
    if _explicit_option("--confirm-actions") or "PORTER_CONFIRM_ACTIONS" in os.environ:
        overrides["confirm_actions"] = args.confirm_actions
    if _explicit_option("--no-hands-free") or "PORTER_HANDS_FREE" in os.environ:
        overrides["hands_free"] = not args.no_hands_free
    if _explicit_option("--voice-silence") or "PORTER_VOICE_SILENCE" in os.environ:
        overrides["voice_silence"] = args.voice_silence
    if _explicit_option("--voice-language") or "PORTER_VOICE_LANGUAGE" in os.environ:
        overrides["voice_language"] = args.voice_language or ""
    if _explicit_option("--stt-model") or "PORTER_STT_MODEL" in os.environ:
        overrides["stt_model"] = args.stt_model
    if _explicit_option("--mic") or "PORTER_MIC" in os.environ:
        overrides["microphone_device"] = args.mic
    if overrides:
        initial = replace(initial, **overrides)

    worker = PorterRuntimeThread(
        initial.runtime_config(),
        initial.voice_config(),
        initial.shortcut_config(),
    )
    porter = PorterViewModel(worker)
    entry = Path(sys.argv[0]).resolve()
    autostart_command = (
        [sys.executable, str(entry)]
        if entry.suffix.casefold() == ".py"
        else [str(entry)]
    )
    autostart = AutostartManager(autostart_command)
    settings_model = PorterSettingsModel(
        worker,
        store=settings_store,
        secrets=secret_store,
        autostart=autostart,
    )
    maintenance = PorterMaintenanceModel()

    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("porter", porter)
    engine.rootContext().setContextProperty("settingsModel", settings_model)
    engine.rootContext().setContextProperty("maintenance", maintenance)
    engine.load(QUrl.fromLocalFile(str(UI_DIR / "Main.qml")))
    engine.load(QUrl.fromLocalFile(str(UI_DIR / "CompactBar.qml")))

    if len(engine.rootObjects()) < 2:
        return 2

    main_window = _find_root(engine, "mainWindow")
    compact_window = _find_root(engine, "compactWindow")

    porter.toggleCompactRequested.connect(
        lambda: _toggle_window(compact_window)
    )
    porter.showMainRequested.connect(
        lambda: _show_window(main_window)
    )
    worker.shortcutActivated.connect(
        lambda: _toggle_window(compact_window)
    )

    tray = None
    if not args.smoke_test and QSystemTrayIcon.isSystemTrayAvailable():
        tray = QSystemTrayIcon(icon, app)
        tray.setToolTip("Porter")

        menu = QMenu()
        open_action = QAction("Open Porter", menu)
        quick_action = QAction("Show Quick Bar", menu)
        listen_action = QAction("Hands-free listening", menu)
        listen_action.setCheckable(True)
        listen_action.setChecked(False)
        menu.addAction(open_action)
        menu.addAction(quick_action)
        menu.addAction(listen_action)
        menu.addSeparator()
        quit_action = QAction("Quit Porter", menu)
        menu.addAction(quit_action)

        open_action.triggered.connect(
            lambda: _show_window(main_window)
        )
        quick_action.triggered.connect(
            lambda: _toggle_window(compact_window)
        )

        def tray_listen_toggled(enabled: bool) -> None:
            if enabled != porter.listening:
                porter.setListening(enabled)

        listen_action.toggled.connect(tray_listen_toggled)

        def sync_listening_action() -> None:
            listen_action.blockSignals(True)
            listen_action.setChecked(porter.listening)
            listen_action.blockSignals(False)

        porter.listeningChanged.connect(sync_listening_action)
        quit_action.triggered.connect(app.quit)
        porter.quitRequested.connect(app.quit)

        def tray_activated(reason) -> None:
            if reason == QSystemTrayIcon.ActivationReason.Trigger:
                _toggle_window(compact_window)

        tray.activated.connect(tray_activated)
        tray.setContextMenu(menu)
        tray.show()
        app.setQuitOnLastWindowClosed(False)
    else:
        # Do not leave an inaccessible background process on desktops without a
        # tray/status notifier implementation.
        app.setQuitOnLastWindowClosed(True)
        porter.quitRequested.connect(app.quit)

    stopping = False

    def shutdown_backend() -> None:
        nonlocal stopping
        if stopping:
            return
        stopping = True
        if worker.isRunning():
            worker.requestShutdown()
            worker.wait(8000)

    app.aboutToQuit.connect(shutdown_backend)

    if args.smoke_test:
        QTimer.singleShot(250, app.quit)
    else:
        worker.start()

    exit_code = app.exec()

    # Keep references alive until after the Qt event loop is gone.
    _ = (
        tray,
        engine,
        porter,
        worker,
        settings_model,
        settings_store,
        secret_store,
        autostart,
        maintenance,
    )
    return int(exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
