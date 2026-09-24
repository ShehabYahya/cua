from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from version import APP_ID


class PorterSingleInstance(QObject):
    """One Porter process per desktop user session.

    Re-launching Porter from the application menu sends an activation message to
    the resident process instead of creating a second Cua/Jev/microphone stack.
    """

    activationRequested = Signal()

    def __init__(
        self,
        server_name: str = APP_ID,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.server_name = server_name
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._accept_connections)
        self._owns_server = False

    @property
    def owns_server(self) -> bool:
        return self._owns_server

    def acquire_or_notify(self, *, timeout_ms: int = 350) -> bool:
        """Return True for the resident instance, False for a re-launch."""
        probe = QLocalSocket()
        probe.connectToServer(self.server_name)
        if probe.waitForConnected(timeout_ms):
            probe.write(b"activate\n")
            probe.flush()
            probe.waitForBytesWritten(timeout_ms)
            probe.disconnectFromServer()
            return False

        # No live server answered. Remove an orphaned endpoint left by a crash
        # before binding the new resident process.
        QLocalServer.removeServer(self.server_name)
        if not self._server.listen(self.server_name):
            raise RuntimeError(
                "Porter could not establish its single-instance endpoint: "
                + self._server.errorString()
            )
        self._owns_server = True
        return True

    def _accept_connections(self) -> None:
        activated = False
        while self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            if socket is None:
                continue
            socket.readAll()
            socket.disconnectFromServer()
            socket.deleteLater()
            activated = True
        if activated:
            self.activationRequested.emit()

    def close(self) -> None:
        if not self._owns_server:
            return
        self._server.close()
        QLocalServer.removeServer(self.server_name)
        self._owns_server = False
