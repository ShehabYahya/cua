import QtQuick
import QtQuick.Controls

ToolButton {
    id: root

    property bool cancelling: false
    signal stopRequested()

    width: 42
    height: 42
    hoverEnabled: true
    focusPolicy: Qt.StrongFocus
    enabled: !cancelling

    Accessible.name: cancelling
        ? "Stopping current Porter task"
        : "Stop current Porter task"

    ToolTip.visible: hovered
    ToolTip.text: Accessible.name
    ToolTip.delay: 450

    contentItem: Item {
        Rectangle {
            anchors.centerIn: parent
            width: 13
            height: 13
            radius: 2
            color: root.enabled ? "#FF879A" : "#9A6170"

            Behavior on opacity {
                NumberAnimation { duration: 120 }
            }
        }
    }

    background: Rectangle {
        radius: 12
        color: root.hovered && root.enabled
            ? Qt.rgba(0.56, 0.10, 0.18, 0.54)
            : Qt.rgba(0.46, 0.08, 0.15, 0.34)
        border.width: 1
        border.color: Qt.rgba(1.0, 0.44, 0.54, root.enabled ? 0.55 : 0.24)
    }

    onClicked: {
        if (!cancelling)
            stopRequested()
    }

    SequentialAnimation on opacity {
        running: root.cancelling
        loops: Animation.Infinite
        NumberAnimation { to: 0.55; duration: 420 }
        NumberAnimation { to: 1.0; duration: 420 }
    }
}
