import QtQuick
import QtQuick.Controls

Switch {
    id: root
    property color accent: typeof settingsModel !== "undefined" ? settingsModel.accentColor : "#49A7FF"
    property bool animationsEnabled: typeof settingsModel !== "undefined" ? settingsModel.animationsEnabled : true

    spacing: 9
    focusPolicy: Qt.StrongFocus

    indicator: Rectangle {
        implicitWidth: 44
        implicitHeight: 24
        x: root.leftPadding
        y: root.topPadding + (root.availableHeight - height) / 2
        radius: height / 2
        color: root.checked
            ? Qt.rgba(root.accent.r, root.accent.g, root.accent.b, 0.72)
            : Qt.rgba(0.22, 0.31, 0.42, 0.72)
        border.width: root.activeFocus ? 2 : 1
        border.color: root.activeFocus
            ? Qt.lighter(root.accent, 1.25)
            : root.checked
                ? root.accent
                : Qt.rgba(0.50, 0.66, 0.82, 0.28)

        Rectangle {
            x: root.checked ? parent.width - width - 3 : 3
            anchors.verticalCenter: parent.verticalCenter
            width: 18
            height: 18
            radius: width / 2
            color: root.enabled ? "#F2F8FF" : "#718096"

            Behavior on x {
                enabled: root.animationsEnabled
                NumberAnimation { duration: 140; easing.type: Easing.OutCubic }
            }
        }

        Behavior on color {
            enabled: root.animationsEnabled
            ColorAnimation { duration: 140 }
        }
    }

    contentItem: Text {
        text: root.text
        color: root.enabled ? "#CBDCF0" : "#67778D"
        font.pixelSize: 12
        verticalAlignment: Text.AlignVCenter
        leftPadding: root.indicator.width + root.spacing
    }
}
