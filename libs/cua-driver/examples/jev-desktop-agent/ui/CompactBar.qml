import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "components"

Window {
    id: root
    objectName: "compactWindow"
    width: 700
    height: 104
    minimumWidth: 540
    maximumWidth: 960
    maximumHeight: 104
    visible: false
    color: "transparent"
    title: "Porter Quick Bar"
    flags: Qt.Window | Qt.FramelessWindowHint

    property bool hovered: hover.hovered
    property bool engaged: porter.busy
        || porter.state === "listening"
        || porter.state === "error"
        || porter.state === "attention"
        || commandField.activeFocus
    property color accent: settingsModel.accentColor
    property real panelOpacity: engaged
        ? 0.94
        : hovered
            ? settingsModel.compactHoverOpacity
            : settingsModel.compactIdleOpacity

    onVisibleChanged: {
        if (visible) {
            Qt.callLater(function() {
                commandField.forceActiveFocus()
            })
        }
    }

    Shortcut {
        sequence: "Escape"
        onActivated: root.hide()
    }

    Shortcut {
        sequence: "Ctrl+L"
        onActivated: {
            commandField.forceActiveFocus()
            commandField.selectAll()
        }
    }

    Behavior on panelOpacity {
        enabled: settingsModel.animationsEnabled
        NumberAnimation { duration: 150; easing.type: Easing.OutCubic }
    }

    readonly property string requestStatus: {
        const detail = porter.detailText || ""
        if (porter.state === "error")
            return detail.length
                ? "Failed — " + detail
                : "Failed — Porter could not complete the request"
        if (porter.state === "attention")
            return detail.length
                ? porter.statusText + " — " + detail
                : porter.statusText
        if (porter.busy)
            return detail.length
                ? porter.statusText + " — " + detail
                : porter.statusText
        if (porter.state === "listening")
            return "Listening…"
        if (porter.statusText === "Done")
            return detail.length ? "Done — " + detail : "Done"
        return porter.listening
            ? "Ready · hands-free listening on"
            : "Ready · microphone muted"
    }

    readonly property color requestStatusColor: {
        if (porter.state === "error")
            return "#FF7188"
        if (porter.state === "attention")
            return "#FFBE67"
        if (porter.busy || porter.state === "listening")
            return root.accent
        if (porter.statusText === "Done")
            return "#70D7B0"
        return "#7890AE"
    }

    onClosing: function(close) {
        close.accepted = false
        root.hide()
    }

    Rectangle {
        id: shadow
        anchors.fill: panel
        anchors.margins: -5
        radius: panel.radius + 5
        color: Qt.rgba(
            root.accent.r,
            root.accent.g,
            root.accent.b,
            root.engaged ? 0.16 : root.hovered ? 0.10 : 0.03
        )
    }

    Rectangle {
        id: panel
        anchors.fill: parent
        anchors.margins: 6
        radius: 23
        color: Qt.rgba(0.025, 0.065, 0.13, root.panelOpacity)
        border.width: 1
        border.color: root.engaged
            ? Qt.rgba(root.accent.r, root.accent.g, root.accent.b, 0.72)
            : root.hovered
                ? Qt.rgba(root.accent.r, root.accent.g, root.accent.b, 0.38)
                : Qt.rgba(root.accent.r, root.accent.g, root.accent.b, 0.08)

        Behavior on color {
            enabled: settingsModel.animationsEnabled
            ColorAnimation { duration: 150 }
        }
        Behavior on border.color {
            enabled: settingsModel.animationsEnabled
            ColorAnimation { duration: 150 }
        }

        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: 10
            anchors.rightMargin: 10
            anchors.topMargin: 9
            anchors.bottomMargin: 9
            spacing: 10

            Item {
                id: moveHandle
                Layout.preferredWidth: 22
                Layout.fillHeight: true

                Accessible.role: Accessible.Grip
                Accessible.name: "Move Quick Bar"

                Text {
                    anchors.centerIn: parent
                    text: "⠿"
                    color: dragHover.hovered ? "#B5C8DF" : "#657C99"
                    font.pixelSize: 18
                }

                HoverHandler {
                    id: dragHover
                    cursorShape: Qt.SizeAllCursor
                }

                DragHandler {
                    target: null
                    acceptedButtons: Qt.LeftButton
                    onActiveChanged: {
                        if (active)
                            root.startSystemMove()
                    }
                }
            }

            PorterRing {
                Layout.preferredWidth: 38
                Layout.preferredHeight: 38
                state: porter.state
                listening: porter.state === "listening"
                accent: settingsModel.accentColor
            }

            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 3

                TextField {
                    id: commandField
                    Layout.fillWidth: true
                    Layout.preferredHeight: 42
                    enabled: !porter.busy
                    placeholderText: porter.state === "listening"
                        ? "Listening…"
                        : porter.listening
                            ? "Type or speak to Porter…"
                            : "Type a command — microphone muted"
                    color: "#000000"
                    placeholderTextColor: "#67778A"
                    font.pixelSize: 15
                    leftPadding: 12
                    rightPadding: 12
                    selectByMouse: true
                    activeFocusOnTab: true

                    Accessible.name: "Porter command"
                    Accessible.description: "Type a desktop task and press Enter to run it"

                    background: Rectangle {
                        radius: 11
                        color: commandField.enabled
                            ? Qt.rgba(0.95, 0.97, 1.0, 0.96)
                            : Qt.rgba(0.86, 0.90, 0.95, 0.82)
                        border.width: commandField.activeFocus ? 1 : 0
                        border.color: root.accent
                    }

                    onAccepted: {
                        const value = text.trim()
                        if (value.length > 0) {
                            porter.submitCommand(value)
                            text = ""
                        }
                    }
                }

                Text {
                    Layout.fillWidth: true
                    text: root.requestStatus
                    color: root.requestStatusColor
                    font.pixelSize: 11
                    font.weight: (
                        porter.state === "error"
                        || porter.state === "attention"
                        || porter.busy
                    ) ? Font.DemiBold : Font.Normal
                    elide: Text.ElideRight
                    maximumLineCount: 1
                    Accessible.name: text
                }
            }

            ToolButton {
                id: voiceButton
                Layout.preferredWidth: 42
                Layout.preferredHeight: 42
                enabled: !porter.busy
                text: porter.listening ? "●" : "◉"
                hoverEnabled: true
                focusPolicy: Qt.StrongFocus
                Accessible.name: porter.listening
                    ? "Mute hands-free listening"
                    : "Enable hands-free listening"
                ToolTip.visible: hovered
                ToolTip.text: Accessible.name
                ToolTip.delay: 550

                contentItem: Text {
                    text: voiceButton.text
                    color: porter.listening ? settingsModel.accentColor : "#8AA3C2"
                    font.pixelSize: porter.listening ? 18 : 17
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }

                background: Rectangle {
                    radius: 12
                    color: voiceButton.hovered
                        ? Qt.rgba(0.10, 0.34, 0.56, 0.28)
                        : "transparent"
                }

                onClicked: porter.toggleListening()
            }

            ToolButton {
                id: mainButton
                Layout.preferredWidth: 42
                Layout.preferredHeight: 42
                text: "⚙"
                hoverEnabled: true
                focusPolicy: Qt.StrongFocus
                Accessible.name: "Open Porter settings"
                ToolTip.visible: hovered
                ToolTip.text: Accessible.name
                ToolTip.delay: 550

                contentItem: Text {
                    text: mainButton.text
                    color: "#849AB8"
                    font.pixelSize: 17
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }

                background: Rectangle {
                    radius: 12
                    color: mainButton.hovered
                        ? Qt.rgba(0.10, 0.34, 0.56, 0.28)
                        : "transparent"
                }

                onClicked: porter.showMain()
            }

            ToolButton {
                id: submitButton
                visible: commandField.text.trim().length > 0 || porter.busy
                Layout.preferredWidth: 42
                Layout.preferredHeight: 42
                text: porter.busy ? "×" : "→"
                hoverEnabled: true
                focusPolicy: Qt.StrongFocus
                Accessible.name: porter.busy ? "Cancel current task" : "Run command"
                ToolTip.visible: hovered
                ToolTip.text: Accessible.name
                ToolTip.delay: 550

                contentItem: Text {
                    text: submitButton.text
                    color: porter.busy ? "#FF8BA0" : "#E9F7FF"
                    font.pixelSize: 20
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }

                background: Rectangle {
                    radius: 12
                    color: porter.busy
                        ? Qt.rgba(0.45, 0.10, 0.16, submitButton.hovered ? 0.48 : 0.30)
                        : Qt.rgba(
                            root.accent.r,
                            root.accent.g,
                            root.accent.b,
                            submitButton.hovered ? 0.82 : 0.64
                        )
                }

                onClicked: {
                    if (porter.busy) {
                        porter.cancelCurrent()
                    } else {
                        const value = commandField.text.trim()
                        if (value.length > 0) {
                            porter.submitCommand(value)
                            commandField.text = ""
                        }
                    }
                }
            }
        }
    }

    HoverHandler {
        id: hover
    }
}
