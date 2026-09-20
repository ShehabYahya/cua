from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QTimer, QUrl
from PySide6.QtGui import QAction, QIcon
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from openrouter_client import (
    DEFAULT_REASONING_MODEL,
    DEFAULT_STT_MODEL,
)
from porter_qt import PorterRuntimeThread, PorterViewModel
from porter_voice import HandsFreeVoiceConfig
from runtime import PorterRuntimeConfig


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
UI_DIR = ROOT / "ui"
ASSETS_DIR = ROOT / "assets"


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Porter native desktop app")
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
    QQuickStyle.setStyle("Basic")

    app = QApplication(sys.argv[:1])
    app.setApplicationDisplayName("Porter")
    app.setApplicationName("Porter")

    icon = QIcon(str(ASSETS_DIR / "porter-ring.svg"))
    app.setWindowIcon(icon)

    config = PorterRuntimeConfig(
        provider=args.provider,
        vision_enabled=not args.no_vision,
        vision_model=args.vision_model,
        writer_model=args.writer_model,
        download_root=args.download_root,
        enforce_policy=args.confirm_actions,
    )
    voice_config = HandsFreeVoiceConfig(
        enabled=not args.no_hands_free,
        stt_model=args.stt_model,
        language=args.voice_language,
        microphone_device=args.mic,
        silence_seconds=args.voice_silence,
    )
    worker = PorterRuntimeThread(config, voice_config)
    porter = PorterViewModel(worker)

    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("porter", porter)
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
    _ = (tray, engine, porter, worker)
    return int(exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
