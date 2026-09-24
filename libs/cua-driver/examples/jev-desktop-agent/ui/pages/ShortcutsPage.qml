import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

Item {
    ColumnLayout {
        anchors.fill: parent
        spacing: 18

        RowLayout {
            Layout.fillWidth: true

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 3

                Text {
                    text: "Shortcuts"
                    color: "#F2F7FF"
                    font.pixelSize: 30
                    font.weight: Font.DemiBold
                }

                Text {
                    text: "Summon Porter's quick bar without switching away from what you are doing."
                    color: "#7F95B2"
                    font.pixelSize: 13
                }
            }

            AuroraButton {
                text: "Revert"
                visible: settingsModel.dirty || settingsModel.applying
                enabled: settingsModel.dirty && !settingsModel.applying
                onClicked: settingsModel.revert()
            }

            AuroraButton {
                text: settingsModel.applying ? "Applying…" : "Apply"
                visible: settingsModel.dirty || settingsModel.applying
                primary: true
                enabled: settingsModel.dirty && !settingsModel.applying
                onClicked: settingsModel.apply()
            }
        }

        AuroraCard {
            Layout.fillWidth: true
            Layout.preferredHeight: 210

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 20
                spacing: 14

                RowLayout {
                    Layout.fillWidth: true

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 3

                        Text {
                            text: "Global quick-bar shortcut"
                            color: "#DDEAFF"
                            font.pixelSize: 15
                            font.weight: Font.DemiBold
                        }

                        Text {
                            text: "Uses the XDG GlobalShortcuts portal on supported Wayland desktops."
                            color: "#7188A7"
                            font.pixelSize: 11
                        }
                    }

                    AuroraSwitch {
                        checked: settingsModel.globalShortcutEnabled
                        onToggled: settingsModel.setGlobalShortcutEnabled(checked)
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 12

                    Text {
                        text: "Preferred trigger"
                        color: "#7188A7"
                        font.pixelSize: 12
                    }

                    TextField {
                        Layout.fillWidth: true
                        text: settingsModel.globalShortcutTrigger
                        placeholderText: "CTRL+ALT+space"
                        enabled: settingsModel.globalShortcutEnabled
                        color: "#E9F4FF"
                        onEditingFinished: settingsModel.setGlobalShortcutTrigger(text)
                    }
                }

                Text {
                    Layout.fillWidth: true
                    text: "Your desktop may show a permission/configuration dialog the first time the shortcut is registered."
                    color: "#6F86A4"
                    font.pixelSize: 11
                    wrapMode: Text.Wrap
                }
            }
        }

        AuroraCard {
            Layout.fillWidth: true
            Layout.preferredHeight: 130
            glassOpacity: 0.54

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 18
                spacing: 8

                Text {
                    text: "Portal status"
                    color: "#DDEAFF"
                    font.pixelSize: 14
                    font.weight: Font.DemiBold
                }

                Text {
                    Layout.fillWidth: true
                    text: porter.shortcutStatus
                    color: porter.shortcutStatus.indexOf("unavailable") >= 0
                        ? "#FF8497"
                        : "#66C4FF"
                    font.pixelSize: 12
                    wrapMode: Text.Wrap
                }
            }
        }

        Item { Layout.fillHeight: true }
    }
}
