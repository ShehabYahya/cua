import QtQuick

Rectangle {
    id: root
    property real glassOpacity: 0.72
    property color accent: typeof settingsModel !== "undefined" ? settingsModel.accentColor : "#49A7FF"
    property bool animationsEnabled: typeof settingsModel !== "undefined" ? settingsModel.animationsEnabled : true

    radius: 18
    color: Qt.rgba(0.035, 0.07, 0.14, glassOpacity)
    border.width: 1
    border.color: Qt.rgba(accent.r, accent.g, accent.b, 0.16)

    Rectangle {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.leftMargin: 18
        anchors.rightMargin: 18
        height: 1
        color: Qt.rgba(0.72, 0.87, 1.0, 0.08)
    }

    Behavior on color {
        enabled: root.animationsEnabled
        ColorAnimation { duration: 160 }
    }
    Behavior on border.color {
        enabled: root.animationsEnabled
        ColorAnimation { duration: 160 }
    }
}
