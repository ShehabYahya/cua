import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "components"
import "pages"

ApplicationWindow {
    id: root
    objectName: "mainWindow"
    width: 1180
    height: 760
    minimumWidth: 980
    minimumHeight: 640
    visible: true
    title: "Porter"
    color: "#07101F"

    property int currentPage: 0
    property color accent: settingsModel.accentColor

    onClosing: function(close) {
        close.accepted = false
        root.hide()
    }

    Rectangle {
        anchors.fill: parent
        color: "#07101F"

        gradient: Gradient {
            orientation: Gradient.Horizontal
            GradientStop { position: 0.0; color: "#07101F" }
            GradientStop { position: 0.48; color: "#09152A" }
            GradientStop { position: 1.0; color: "#06101D" }
        }

        Rectangle {
            width: 520
            height: 520
            radius: width / 2
            x: parent.width - 340
            y: -280
            color: Qt.rgba(0.08, 0.42, 0.78, 0.08)
        }

        RowLayout {
            anchors.fill: parent
            spacing: 0

            Rectangle {
                Layout.preferredWidth: 236
                Layout.fillHeight: true
                color: Qt.rgba(0.025, 0.055, 0.105, 0.92)
                border.width: 1
                border.color: Qt.rgba(0.30, 0.64, 1.0, 0.10)

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 18
                    spacing: 12

                    Item {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 72

                        Row {
                            anchors.verticalCenter: parent.verticalCenter
                            spacing: 12

                            PorterRing {
                                width: 42
                                height: 42
                                state: porter.state
                                listening: porter.state === "listening"
                                accent: settingsModel.accentColor
                            }

                            Column {
                                anchors.verticalCenter: parent.verticalCenter
                                spacing: 1

                                Text {
                                    text: "PORTER"
                                    color: "#F3F8FF"
                                    font.pixelSize: 18
                                    font.weight: Font.DemiBold
                                    font.letterSpacing: 1.4
                                }

                                Text {
                                    text: "Desktop intelligence"
                                    color: "#6F86A5"
                                    font.pixelSize: 11
                                }
                            }
                        }
                    }

                    Repeater {
                        model: [
                            "Home",
                            "Voice & Audio",
                            "Models",
                            "Computer Control",
                            "Shortcuts",
                            "Personalization",
                            "Appearance",
                            "Advanced"
                        ]

                        delegate: Rectangle {
                            required property int index
                            required property string modelData

                            Layout.fillWidth: true
                            Layout.preferredHeight: 44
                            radius: 12
                            color: root.currentPage === index
                                ? Qt.rgba(
                                    root.accent.r,
                                    root.accent.g,
                                    root.accent.b,
                                    0.22
                                )
                                : navHover.hovered
                                    ? Qt.rgba(0.20, 0.38, 0.58, 0.12)
                                    : "transparent"
                            border.width: root.currentPage === index ? 1 : 0
                            border.color: Qt.rgba(
                                root.accent.r,
                                root.accent.g,
                                root.accent.b,
                                0.35
                            )

                            Text {
                                anchors.left: parent.left
                                anchors.leftMargin: 14
                                anchors.verticalCenter: parent.verticalCenter
                                text: modelData
                                color: root.currentPage === index ? "#E8F5FF" : "#8DA3BF"
                                font.pixelSize: 14
                                font.weight: root.currentPage === index
                                    ? Font.DemiBold
                                    : Font.Normal
                            }

                            HoverHandler { id: navHover }

                            TapHandler {
                                onTapped: root.currentPage = index
                            }

                            Behavior on color {
                                ColorAnimation { duration: 130 }
                            }
                        }
                    }

                    Item { Layout.fillHeight: true }

                    AuroraCard {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 94
                        glassOpacity: 0.52

                        Row {
                            anchors.fill: parent
                            anchors.margins: 14
                            spacing: 10

                            PorterRing {
                                width: 32
                                height: 32
                                state: porter.state
                                listening: porter.state === "listening"
                                accent: settingsModel.accentColor
                            }

                            Column {
                                anchors.verticalCenter: parent.verticalCenter
                                width: parent.width - 50
                                spacing: 4

                                Text {
                                    width: parent.width
                                    text: porter.statusText
                                    color: "#DCEAFF"
                                    font.pixelSize: 13
                                    font.weight: Font.DemiBold
                                    elide: Text.ElideRight
                                }

                                Text {
                                    width: parent.width
                                    text: porter.busy ? "Porter is acting" : "Resident runtime active"
                                    color: "#7087A6"
                                    font.pixelSize: 11
                                    elide: Text.ElideRight
                                }
                            }
                        }
                    }
                }
            }

            Item {
                Layout.fillWidth: true
                Layout.fillHeight: true

                StackLayout {
                    anchors.fill: parent
                    anchors.margins: 34
                    currentIndex: root.currentPage

                    Item {
                        ColumnLayout {
                            anchors.fill: parent
                            spacing: 22

                            RowLayout {
                                Layout.fillWidth: true

                                ColumnLayout {
                                    spacing: 4
                                    Layout.fillWidth: true

                                    Text {
                                        text: settingsModel.preferredName.length
                                            ? "Good to see you, " + settingsModel.preferredName
                                            : "Good to see you"
                                        color: "#F2F7FF"
                                        font.pixelSize: 31
                                        font.weight: Font.DemiBold
                                    }

                                    Text {
                                        text: "Porter is ready whenever you are."
                                        color: "#7F95B2"
                                        font.pixelSize: 15
                                    }
                                }

                                AuroraButton {
                                    text: "Quick Bar"
                                    onClicked: porter.toggleCompact()
                                }
                            }

                            AuroraCard {
                                Layout.fillWidth: true
                                Layout.preferredHeight: 186
                                glassOpacity: 0.78

                                ColumnLayout {
                                    anchors.fill: parent
                                    anchors.margins: 22
                                    spacing: 14

                                    RowLayout {
                                        Layout.fillWidth: true

                                        PorterRing {
                                            width: 40
                                            height: 40
                                            state: porter.state
                                            listening: porter.state === "listening"
                                            accent: settingsModel.accentColor
                                        }

                                        ColumnLayout {
                                            Layout.fillWidth: true
                                            spacing: 2

                                            Text {
                                                text: porter.statusText
                                                color: "#E8F3FF"
                                                font.pixelSize: 17
                                                font.weight: Font.DemiBold
                                            }

                                            Text {
                                                Layout.fillWidth: true
                                                text: porter.detailText
                                                color: "#7890AE"
                                                font.pixelSize: 12
                                                elide: Text.ElideRight
                                            }
                                        }
                                    }

                                    TextField {
                                        id: commandField
                                        Layout.fillWidth: true
                                        Layout.preferredHeight: 58
                                        placeholderText: "Ask Porter to do anything on your computer…"
                                        color: "#E9F4FF"
                                        placeholderTextColor: "#5F7593"
                                        font.pixelSize: 15
                                        leftPadding: 18
                                        rightPadding: 128
                                        enabled: !porter.busy
                                        selectByMouse: true

                                        background: Rectangle {
                                            radius: 15
                                            color: Qt.rgba(0.025, 0.07, 0.13, 0.88)
                                            border.width: commandField.activeFocus ? 1 : 1
                                            border.color: commandField.activeFocus
                                                ? settingsModel.accentColor
                                                : Qt.rgba(0.34, 0.64, 0.92, 0.16)

                                            Behavior on border.color {
                                                enabled: settingsModel.animationsEnabled
                                                ColorAnimation { duration: 140 }
                                            }
                                        }

                                        onAccepted: {
                                            const value = text.trim()
                                            if (value.length > 0) {
                                                porter.submitCommand(value)
                                                text = ""
                                            }
                                        }

                                        AuroraButton {
                                            anchors.right: parent.right
                                            anchors.rightMargin: 8
                                            anchors.verticalCenter: parent.verticalCenter
                                            width: 104
                                            text: porter.busy ? "Working" : "Run"
                                            primary: true
                                            enabled: !porter.busy
                                            onClicked: commandField.accepted()
                                        }
                                    }
                                }
                            }

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 14

                                AuroraCard {
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: 132

                                    ColumnLayout {
                                        anchors.fill: parent
                                        anchors.margins: 18
                                        spacing: 8

                                        Text {
                                            text: "Last command"
                                            color: "#6F86A4"
                                            font.pixelSize: 11
                                            font.weight: Font.DemiBold
                                        }

                                        Text {
                                            Layout.fillWidth: true
                                            Layout.fillHeight: true
                                            text: porter.lastCommand.length
                                                ? porter.lastCommand
                                                : "Nothing yet — type a command above."
                                            color: porter.lastCommand.length ? "#D9E8FB" : "#60748F"
                                            font.pixelSize: 14
                                            wrapMode: Text.Wrap
                                        }
                                    }
                                }

                                AuroraCard {
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: 132

                                    ColumnLayout {
                                        anchors.fill: parent
                                        anchors.margins: 18
                                        spacing: 8

                                        Text {
                                            text: "Hands-free voice"
                                            color: "#6F86A4"
                                            font.pixelSize: 11
                                            font.weight: Font.DemiBold
                                        }

                                        Text {
                                            text: porter.listening
                                                ? "Always listening locally"
                                                : "Microphone muted"
                                            color: porter.listening ? "#7FC7FF" : "#D9E8FB"
                                            font.pixelSize: 14
                                        }

                                        AuroraButton {
                                            text: porter.listening ? "Mute" : "Enable"
                                            enabled: !porter.busy
                                            onClicked: porter.toggleListening()
                                        }
                                    }
                                }
                            }

                            AuroraCard {
                                Layout.fillWidth: true
                                Layout.fillHeight: true
                                glassOpacity: 0.46

                                ColumnLayout {
                                    anchors.fill: parent
                                    anchors.margins: 20
                                    spacing: 12

                                    Text {
                                        text: "Runtime"
                                        color: "#DDEAFF"
                                        font.pixelSize: 16
                                        font.weight: Font.DemiBold
                                    }

                                    Text {
                                        Layout.fillWidth: true
                                        text: "The native shell is connected directly to the persistent PorterRuntime. Closing this window hides the interface; it does not terminate the backend."
                                        color: "#7188A7"
                                        font.pixelSize: 13
                                        wrapMode: Text.Wrap
                                    }

                                    Item { Layout.fillHeight: true }

                                    RowLayout {
                                        Layout.fillWidth: true

                                        AuroraButton {
                                            text: "Cancel current task"
                                            enabled: porter.busy
                                            onClicked: porter.cancelCurrent()
                                        }

                                        Item { Layout.fillWidth: true }

                                        Text {
                                            text: porter.state.toUpperCase()
                                            color: porter.state === "error" ? "#FF758C" : "#5CB6F7"
                                            font.pixelSize: 11
                                            font.weight: Font.Bold
                                            font.letterSpacing: 1.2
                                        }
                                    }
                                }
                            }
                        }
                    }

                    VoicePage {
                    }

                    ModelsPage {
                    }

                    ComputerPage {
                    }

                    ShortcutsPage {
                    }

                    PersonalizationPage {
                    }

                    AppearancePage {
                    }

                    AdvancedPage {
                    }
                }
            }
        }
    }

    Onboarding {
    }
}
