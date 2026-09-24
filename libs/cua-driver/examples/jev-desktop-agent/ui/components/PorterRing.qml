import QtQuick

Item {
    id: root
    property string state: "ready"
    property bool listening: false
    property color accent: typeof settingsModel !== "undefined" ? settingsModel.accentColor : "#49A7FF"
    property bool animationsEnabled: typeof settingsModel !== "undefined" ? settingsModel.animationsEnabled : true
    property color errorAccent: "#FF647C"
    property color attentionAccent: "#FFB85C"

    implicitWidth: 40
    implicitHeight: 40

    readonly property color ringColor: {
        if (state === "error")
            return errorAccent
        if (state === "attention")
            return attentionAccent
        return accent
    }

    Rectangle {
        id: halo
        anchors.centerIn: parent
        width: parent.width
        height: parent.height
        radius: width / 2
        color: "transparent"
        border.width: 2
        border.color: root.ringColor
        opacity: root.state === "starting" ? 0.55 : 0.9

        Behavior on border.color {
            enabled: root.animationsEnabled
            ColorAnimation { duration: 180 }
        }
        Behavior on opacity {
            enabled: root.animationsEnabled
            NumberAnimation { duration: 180 }
        }
    }

    Rectangle {
        anchors.centerIn: parent
        width: Math.max(4, parent.width * 0.12)
        height: width
        radius: width / 2
        color: root.ringColor
        opacity: root.state === "ready" ? 0.38 : 0.78

        Behavior on color {
            enabled: root.animationsEnabled
            ColorAnimation { duration: 180 }
        }
    }

    Rectangle {
        anchors.centerIn: parent
        width: parent.width - 10
        height: width
        radius: width / 2
        color: "#0A1224"
        border.width: 1
        border.color: Qt.rgba(0.35, 0.66, 1.0, 0.25)
    }

    Item {
        id: orbit
        anchors.fill: parent

        Rectangle {
            id: runner
            width: 7
            height: 7
            radius: width / 2
            color: root.ringColor
            anchors.horizontalCenter: parent.horizontalCenter
            y: 0
            opacity: root.state === "working"
                || root.state === "starting"
                || root.listening ? 1.0 : 0.0
        }

        RotationAnimator {
            target: orbit
            from: 0
            to: 360
            duration: root.listening ? 900 : 1500
            loops: Animation.Infinite
            running: root.animationsEnabled && (
                root.state === "working"
                || root.state === "starting"
                || root.listening
            )
        }
    }

    SequentialAnimation on scale {
        loops: Animation.Infinite
        running: root.animationsEnabled && root.listening
        NumberAnimation { to: 1.08; duration: 420; easing.type: Easing.InOutSine }
        NumberAnimation { to: 1.0; duration: 420; easing.type: Easing.InOutSine }
    }
}
