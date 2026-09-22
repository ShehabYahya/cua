import QtQuick
import QtQuick.Controls

Slider {
    id: root
    property color accent: typeof settingsModel !== "undefined" ? settingsModel.accentColor : "#49A7FF"
    property bool animationsEnabled: typeof settingsModel !== "undefined" ? settingsModel.animationsEnabled : true

    focusPolicy: Qt.StrongFocus

    background: Rectangle {
        x: root.leftPadding
        y: root.topPadding + root.availableHeight / 2 - height / 2
        implicitWidth: 200
        implicitHeight: 5
        width: root.availableWidth
        height: implicitHeight
        radius: height / 2
        color: Qt.rgba(0.22, 0.31, 0.42, 0.78)

        Rectangle {
            width: root.visualPosition * parent.width
            height: parent.height
            radius: parent.radius
            color: root.accent
        }
    }

    handle: Rectangle {
        x: root.leftPadding + root.visualPosition * (root.availableWidth - width)
        y: root.topPadding + root.availableHeight / 2 - height / 2
        implicitWidth: 18
        implicitHeight: 18
        radius: width / 2
        color: root.pressed ? Qt.lighter(root.accent, 1.18) : "#EAF5FF"
        border.width: root.activeFocus ? 2 : 1
        border.color: root.activeFocus ? root.accent : Qt.rgba(0.15, 0.45, 0.72, 0.60)

        Behavior on x {
            enabled: root.animationsEnabled && !root.pressed
            NumberAnimation { duration: 90; easing.type: Easing.OutCubic }
        }
    }
}
