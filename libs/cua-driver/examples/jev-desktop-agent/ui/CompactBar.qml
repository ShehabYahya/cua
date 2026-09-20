import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "components"

Window {
    id: root
    objectName: "compactWindow"
    width: 680
    height: 82
    minimumWidth: 520
    maximumHeight: 82
    visible: false
    color: "transparent"
    title: "Porter Quick Bar"
    flags: Qt.Window | Qt.FramelessWindowHint

    property bool hovered: hover.hovered
    property bool engaged: porter.busy || porter.listening || commandField.activeFocus
    property real panelOpacity: engaged ? 0.92 : hovered ? 0.72 : 0.26

    onClosing: function(close) {
        close.accepted = false
        root.hide()
    }

    Rectangle {
        id: shadow
        anchors.fill: panel
        anchors.margins: -5
        radius: panel.radius + 5
        color: Qt.rgba(0.0, 0.25, 0.55, root.engaged ? 0.16 : root.hovered ? 0.10 : 0.03)
    }

    Rectangle {
        id: panel
        anchors.fill: parent
        anchors.margins: 6
        radius: 23
        color: Qt.rgba(0.025, 0.065, 0.13, root.panelOpacity)
        border.width: 1
        border.color: root.engaged
            ? Qt.rgba(0.25, 0.67, 1.0, 0.72)
            : root.hovered
                ? Qt.rgba(0.25, 0.67, 1.0, 0.38)
                : Qt.rgba(0.28, 0.62, 0.95, 0.08)

        Behavior on color {
            ColorAnimation { duration: 150 }
        }
        Behavior on border.color {
            ColorAnimation { duration: 150 }
        }

        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: 16
            anchors.rightMargin: 10
            anchors.topMargin: 10
            anchors.bottomMargin: 10
            spacing: 12

            PorterRing {
                Layout.preferredWidth: 38
                Layout.preferredHeight: 38
                state: porter.state
                listening: porter.listening
            }

            TextField {
                id: commandField
                Layout.fillWidth: true
                Layout.fillHeight: true
                enabled: !porter.busy
                placeholderText: porter.listening
                    ? "Listening…"
                    : porter.busy
                        ? porter.statusText
                        : "Type or speak to Porter…"
                color: "#EDF6FF"
                placeholderTextColor: root.engaged || root.hovered ? "#8299B7" : "#5C718D"
                font.pixelSize: 15
                selectByMouse: true
                background: Item {}

                onAccepted: {
                    const value = text.trim()
                    if (value.length > 0) {
                        porter.submitCommand(value)
                        text = ""
                    }
                }
            }

            ToolButton {
                id: voiceButton
                Layout.preferredWidth: 42
                Layout.preferredHeight: 42
                enabled: !porter.busy
                text: porter.listening ? "●" : "◉"

                contentItem: Text {
                    text: voiceButton.text
                    color: porter.listening ? "#5FC4FF" : "#8AA3C2"
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
                        : Qt.rgba(0.08, 0.48, 0.80, submitButton.hovered ? 0.72 : 0.52)
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
