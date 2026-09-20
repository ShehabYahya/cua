from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import (
    QObject,
    Property,
    QProcess,
    QStandardPaths,
    Signal,
    Slot,
    QUrl,
)
from PySide6.QtGui import QDesktopServices
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply

from version import (
    CUA_DRIVER_DOCS_URL,
    CUA_DRIVER_INSTALL_SCRIPT,
    RELEASES_API,
    RELEASES_URL,
    RELEASE_TAG_PREFIX,
    __version__,
)


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    tag: str
    url: str


def version_key(value: str) -> tuple[int, ...]:
    parts = [int(item) for item in re.findall(r"\d+", value)]
    return tuple(parts[:4]) or (0,)


def latest_porter_release(payload: Any) -> ReleaseInfo | None:
    if not isinstance(payload, list):
        return None

    releases: list[ReleaseInfo] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        if item.get("draft") is True:
            continue
        tag = str(item.get("tag_name") or "")
        if not tag.startswith(RELEASE_TAG_PREFIX):
            continue
        version = tag[len(RELEASE_TAG_PREFIX) :].strip()
        url = str(item.get("html_url") or "")
        if not version:
            continue
        releases.append(ReleaseInfo(version, tag, url))

    if not releases:
        return None
    return max(releases, key=lambda item: version_key(item.version))


class PorterMaintenanceModel(QObject):
    """Native host-maintenance surface for Cua Driver and Porter releases."""

    driverChanged = Signal()
    updateChanged = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._driver_path = ""
        self._driver_version = ""
        self._driver_status = "Not checked"
        self._driver_output = ""
        self._driver_busy = False
        self._driver_operation = ""

        self._update_status = "Not checked"
        self._latest_version = ""
        self._release_url = RELEASES_URL
        self._update_checking = False

        self._process = QProcess(self)
        self._process.setProcessChannelMode(
            QProcess.ProcessChannelMode.MergedChannels
        )
        self._process.readyReadStandardOutput.connect(
            self._process_output_ready
        )
        self._process.finished.connect(self._process_finished)
        self._process.errorOccurred.connect(self._process_error)

        self._network = QNetworkAccessManager(self)

        self.refreshDriver()

    @Property(str, constant=True)
    def currentVersion(self) -> str:
        return __version__

    @Property(bool, notify=driverChanged)
    def driverInstalled(self) -> bool:
        return bool(self._driver_path)

    @Property(str, notify=driverChanged)
    def driverPath(self) -> str:
        return self._driver_path

    @Property(str, notify=driverChanged)
    def driverVersion(self) -> str:
        return self._driver_version

    @Property(str, notify=driverChanged)
    def driverStatus(self) -> str:
        return self._driver_status

    @Property(str, notify=driverChanged)
    def driverOutput(self) -> str:
        return self._driver_output

    @Property(bool, notify=driverChanged)
    def driverBusy(self) -> bool:
        return self._driver_busy

    @Property(str, notify=updateChanged)
    def updateStatus(self) -> str:
        return self._update_status

    @Property(str, notify=updateChanged)
    def latestVersion(self) -> str:
        return self._latest_version

    @Property(bool, notify=updateChanged)
    def updateAvailable(self) -> bool:
        return bool(
            self._latest_version
            and version_key(self._latest_version)
            > version_key(__version__)
        )

    @Property(str, notify=updateChanged)
    def releaseUrl(self) -> str:
        return self._release_url

    @Property(bool, notify=updateChanged)
    def updateChecking(self) -> bool:
        return self._update_checking

    def _set_driver_state(
        self,
        *,
        status: str | None = None,
        output: str | None = None,
        busy: bool | None = None,
    ) -> None:
        changed = False
        if status is not None and status != self._driver_status:
            self._driver_status = status
            changed = True
        if output is not None and output != self._driver_output:
            self._driver_output = output
            changed = True
        if busy is not None and bool(busy) != self._driver_busy:
            self._driver_busy = bool(busy)
            changed = True
        if changed:
            self.driverChanged.emit()

    @Slot()
    def refreshDriver(self) -> None:
        if self._driver_busy:
            return
        path = QStandardPaths.findExecutable("cua-driver")
        self._driver_path = path
        self._driver_version = ""
        if not path:
            self._driver_status = "Cua Driver is not installed"
            self._driver_output = (
                "Porter could not find the cua-driver command in PATH."
            )
            self.driverChanged.emit()
            return
        self.driverChanged.emit()
        self._run_driver("version", ["--version"])

    def _run_driver(self, operation: str, args: list[str]) -> None:
        if self._driver_busy:
            return
        path = self._driver_path or QStandardPaths.findExecutable(
            "cua-driver"
        )
        if not path:
            self._driver_path = ""
            self._set_driver_state(
                status="Cua Driver is not installed",
                output="Install Cua Driver before running this command.",
            )
            return

        self._driver_path = path
        self._driver_operation = operation
        self._driver_output = ""
        self._set_driver_state(
            status={
                "version": "Reading Cua Driver version…",
                "doctor": "Running Cua Driver doctor…",
                "update": "Updating Cua Driver…",
            }.get(operation, "Running Cua Driver…"),
            busy=True,
        )
        self._process.start(path, args)

    @Slot()
    def doctorDriver(self) -> None:
        self._run_driver("doctor", ["doctor"])

    @Slot()
    def updateDriver(self) -> None:
        self._run_driver("update", ["update", "--apply"])

    @Slot()
    def installDriver(self) -> None:
        if self._driver_busy:
            return
        bash = QStandardPaths.findExecutable("bash")
        curl = QStandardPaths.findExecutable("curl")
        if not bash or not curl:
            self._set_driver_state(
                status="Installer unavailable",
                output=(
                    "Porter's guided installer requires bash and curl. "
                    "Open the official Cua Driver installation guide instead."
                ),
            )
            return

        self._driver_operation = "install"
        self._driver_output = ""
        self._set_driver_state(
            status="Installing Cua Driver…",
            busy=True,
        )
        script = (
            f'"{curl}" -fsSL "{CUA_DRIVER_INSTALL_SCRIPT}" '
            "| /bin/bash"
        )
        self._process.start(bash, ["-c", script])

    @Slot()
    def openDriverDocs(self) -> None:
        QDesktopServices.openUrl(QUrl(CUA_DRIVER_DOCS_URL))

    @Slot()
    def openReleasePage(self) -> None:
        QDesktopServices.openUrl(
            QUrl(self._release_url or RELEASES_URL)
        )

    @Slot()
    def checkPorterUpdates(self) -> None:
        if self._update_checking:
            return
        self._update_checking = True
        self._update_status = "Checking GitHub releases…"
        self.updateChanged.emit()

        request = QNetworkRequest(QUrl(RELEASES_API))
        request.setRawHeader(b"Accept", b"application/vnd.github+json")
        request.setRawHeader(
            b"User-Agent",
            f"Porter/{__version__}".encode("ascii"),
        )
        reply = self._network.get(request)
        reply.finished.connect(lambda: self._update_reply_finished(reply))

    def _update_reply_finished(self, reply: QNetworkReply) -> None:
        try:
            error = reply.error()
            if error != QNetworkReply.NetworkError.NoError:
                self._update_status = (
                    "Update check failed: " + reply.errorString()
                )
                self._latest_version = ""
                self._release_url = RELEASES_URL
                return

            raw = bytes(reply.readAll())
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._update_status = "GitHub returned an invalid update response."
                self._latest_version = ""
                self._release_url = RELEASES_URL
                return

            release = latest_porter_release(payload)
            if release is None:
                self._latest_version = ""
                self._release_url = RELEASES_URL
                self._update_status = (
                    "No published Porter release was found yet."
                )
                return

            self._latest_version = release.version
            self._release_url = release.url or RELEASES_URL
            if version_key(release.version) > version_key(__version__):
                self._update_status = (
                    f"Porter {release.version} is available."
                )
            elif version_key(release.version) == version_key(__version__):
                self._update_status = (
                    f"Porter {__version__} is up to date."
                )
            else:
                self._update_status = (
                    f"This build ({__version__}) is newer than the latest "
                    f"published release ({release.version})."
                )
        finally:
            self._update_checking = False
            self.updateChanged.emit()
            reply.deleteLater()

    @Slot()
    def cancelDriverOperation(self) -> None:
        if self._driver_busy:
            self._process.kill()

    @Slot()
    def _process_output_ready(self) -> None:
        text = bytes(
            self._process.readAllStandardOutput()
        ).decode("utf-8", errors="replace")
        if not text:
            return
        self._driver_output = (
            self._driver_output + text
        )[-12000:]
        self.driverChanged.emit()

    @Slot(int, QProcess.ExitStatus)
    def _process_finished(
        self,
        exit_code: int,
        exit_status: QProcess.ExitStatus,
    ) -> None:
        operation = self._driver_operation
        tail = self._driver_output.strip()
        success = (
            exit_status == QProcess.ExitStatus.NormalExit
            and exit_code == 0
        )

        if operation == "version" and success:
            first = tail.splitlines()[0] if tail else "Installed"
            self._driver_version = first
            self._driver_status = "Cua Driver ready"
        elif operation == "doctor":
            self._driver_status = (
                "Cua Driver doctor completed"
                if success
                else "Cua Driver doctor found a problem"
            )
        elif operation == "update":
            self._driver_status = (
                "Cua Driver update completed"
                if success
                else "Cua Driver update failed"
            )
        elif operation == "install":
            self._driver_status = (
                "Cua Driver installation completed"
                if success
                else "Cua Driver installation failed"
            )
        elif not success:
            self._driver_status = "Cua Driver command failed"

        self._driver_busy = False
        self._driver_operation = ""
        self.driverChanged.emit()

        if success and operation in {"install", "update"}:
            self.refreshDriver()

    @Slot(QProcess.ProcessError)
    def _process_error(self, error: QProcess.ProcessError) -> None:
        _ = error
        self._driver_status = (
            "Cua Driver command could not be started"
        )
        self._driver_output = (
            self._driver_output
            + "\n"
            + self._process.errorString()
        ).strip()
        self._driver_busy = False
        self._driver_operation = ""
        self.driverChanged.emit()
