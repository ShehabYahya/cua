from __future__ import annotations

import argparse
import os
import sys
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QRect, QTimer, QUrl
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
from single_instance import PorterSingleInstance
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


def _sync_tray_actions(porter, stop_action, listen_action) -> None:
    stop_action.setText(
        "Stopping current task…"
        if porter.cancelling
        else "Stop current task"
    )
    stop_action.setEnabled(porter.busy and not porter.cancelling)

    listen_action.blockSignals(True)
    try:
        listen_action.setChecked(porter.handsFreeActive)
        listen_action.setEnabled(
            not porter.manualVoiceActive
            and not porter.busy
            and not porter.cancelling
        )
    finally:
        listen_action.blockSignals(False)


def _clamp_window_position(
    x: int,
    y: int,
    width: int,
    height: int,
    screens: list[QRect],
) -> tuple[int, int]:
    """Keep a restored window fully reachable after monitor changes."""

    if not screens:
        return int(x), int(y)
    center_x = int(x) + max(1, int(width)) // 2
    center_y = int(y) + max(1, int(height)) // 2
    screen = next(
        (
            rect
            for rect in screens
            if rect.contains(center_x, center_y)
        ),
        screens[0],
    )
    maximum_x = max(screen.left(), screen.right() - max(1, int(width)) + 1)
    maximum_y = max(screen.top(), screen.bottom() - max(1, int(height)) + 1)
    return (
        max(screen.left(), min(int(x), maximum_x)),
        max(screen.top(), min(int(y), maximum_y)),
    )


def _restore_window_state(settings, main_window, compact_window) -> None:
    screens = [screen.availableGeometry() for screen in QGuiApplication.screens()]
    primary = screens[0] if screens else QRect(0, 0, 1280, 720)
    if settings.contains("windows/main_x"):
        try:
            width = max(
                main_window.minimumWidth(),
                int(settings.value("windows/main_width", main_window.width())),
            )
            height = max(
                main_window.minimumHeight(),
                int(settings.value("windows/main_height", main_window.height())),
            )
            width = min(width, primary.width())
            height = min(height, primary.height())
            x, y = _clamp_window_position(
                int(settings.value("windows/main_x", primary.left())),
                int(settings.value("windows/main_y", primary.top())),
                width,
                height,
                screens,
            )
            main_window.setWidth(width)
            main_window.setHeight(height)
            main_window.setX(x)
            main_window.setY(y)
        except (TypeError, ValueError):
            pass

    default_x = primary.right() - compact_window.width() - 36
    default_y = primary.top() + 48
    try:
        x = int(settings.value("windows/compact_x", default_x))
        y = int(settings.value("windows/compact_y", default_y))
    except (TypeError, ValueError):
        x, y = default_x, default_y
    x, y = _clamp_window_position(
        x,
        y,
        compact_window.width(),
        compact_window.height(),
        screens,
    )
    compact_window.setX(x)
    compact_window.setY(y)


def _save_window_state(settings, main_window, compact_window) -> None:
    settings.setValue("windows/main_x", main_window.x())
    settings.setValue("windows/main_y", main_window.y())
    settings.setValue("windows/main_width", main_window.width())
    settings.setValue("windows/main_height", main_window.height())
    settings.setValue("windows/compact_x", compact_window.x())
    settings.setValue("windows/compact_y", compact_window.y())
    settings.sync()


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

    single_instance = PorterSingleInstance()
    if not args.smoke_test and not single_instance.acquire_or_notify():
        return 0

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
        parent=app,
    )
    porter = PorterViewModel(worker, parent=app)
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
        parent=app,
    )
    maintenance = PorterMaintenanceModel(parent=app)

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
    _restore_window_state(
        settings_store.settings,
        main_window,
        compact_window,
    )

    def remember_compact_position() -> None:
        if compact_window.isVisible():
            return
        settings_store.settings.setValue(
            "windows/compact_x",
            compact_window.x(),
        )
        settings_store.settings.setValue(
            "windows/compact_y",
            compact_window.y(),
        )
        settings_store.settings.sync()

    compact_window.visibleChanged.connect(remember_compact_position)

    porter.toggleCompactRequested.connect(
        lambda: _toggle_window(compact_window)
    )
    porter.showMainRequested.connect(
        lambda: _show_window(main_window)
    )
    worker.shortcutActivated.connect(
        lambda: _toggle_window(compact_window)
    )
    single_instance.activationRequested.connect(
        lambda: _show_window(main_window)
    )

    tray = None
    if not args.smoke_test and QSystemTrayIcon.isSystemTrayAvailable():
        tray = QSystemTrayIcon(icon, app)
        tray.setToolTip("Porter")

        menu = QMenu()
        open_action = QAction("Open Porter", menu)
        quick_action = QAction("Show Quick Bar", menu)
        stop_action = QAction("Stop current task", menu)
        stop_action.setEnabled(False)
        listen_action = QAction("Hands-free listening", menu)
        listen_action.setCheckable(True)
        listen_action.setChecked(False)
        menu.addAction(open_action)
        menu.addAction(quick_action)
        menu.addAction(stop_action)
        menu.addSeparator()
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
        stop_action.triggered.connect(porter.cancelCurrent)

        def sync_tray_actions() -> None:
            _sync_tray_actions(
                porter,
                stop_action,
                listen_action,
            )

        porter.busyChanged.connect(sync_tray_actions)
        porter.cancellingChanged.connect(sync_tray_actions)
        porter.listeningChanged.connect(sync_tray_actions)
        porter.manualVoiceActiveChanged.connect(sync_tray_actions)
        sync_tray_actions()

        def tray_listen_toggled(enabled: bool) -> None:
            if enabled != porter.handsFreeActive:
                porter.setListening(enabled)

        listen_action.toggled.connect(tray_listen_toggled)
        quit_action.triggered.connect(app.quit)
        porter.quitRequested.connect(app.quit)

        def tray_activated(reason) -> None:
            if reason == QSystemTrayIcon.ActivationReason.Trigger:
                _toggle_window(compact_window)

        tray.activated.connect(tray_activated)
        tray.setContextMenu(menu)
        tray.show()
    else:
        porter.quitRequested.connect(app.quit)

    # Porter is a resident desktop application. If the tray/status notifier is
    # unavailable, re-launching Porter from the app menu activates this same
    # process through the single-instance endpoint.
    app.setQuitOnLastWindowClosed(bool(args.smoke_test))

    stopping = False

    def shutdown_backend() -> None:
        nonlocal stopping
        if stopping:
            return
        stopping = True
        _save_window_state(
            settings_store.settings,
            main_window,
            compact_window,
        )
        single_instance.close()
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
        single_instance,
    )
    return int(exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
