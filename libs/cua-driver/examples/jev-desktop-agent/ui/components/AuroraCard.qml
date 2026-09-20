import QtQuick

Rectangle {
    id: root
    property real glassOpacity: 0.72

    radius: 18
    color: Qt.rgba(0.035, 0.07, 0.14, glassOpacity)
    border.width: 1
    border.color: Qt.rgba(0.28, 0.62, 1.0, 0.16)

    Behavior on color {
        ColorAnimation { duration: 160 }
    }
    Behavior on border.color {
        ColorAnimation { duration: 160 }
    }
}
