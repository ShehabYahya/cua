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
                    text: "Computer Control"
                    color: "#F2F7FF"
                    font.pixelSize: 30
                    font.weight: Font.DemiBold
                }

                Text {
                    text: "Control how Porter is allowed to interact with your desktop."
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
            Layout.preferredHeight: 184

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 20
                spacing: 12

                RowLayout {
                    Layout.fillWidth: true

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 3

                        Text {
                            text: "Visual click binding"
                            color: "#DDEAFF"
                            font.pixelSize: 15
                            font.weight: Font.DemiBold
                        }

                        Text {
                            text: settingsModel.visualClickMode === "strict"
                                ? "Strict requires capture-bound visual clicks."
                                : "Permissive also allows Driver-supported coordinates without capture binding."
                            color: "#7188A7"
                            font.pixelSize: 12
                            wrapMode: Text.Wrap
                            Layout.fillWidth: true
                        }
                    }

                    AuroraComboBox {
                        id: visualMode
                        Layout.preferredWidth: 170
                        model: ["Strict", "Permissive"]
                        currentIndex: settingsModel.visualClickMode === "permissive" ? 1 : 0
                        onActivated: settingsModel.setVisualClickMode(
                            currentIndex === 1 ? "permissive" : "strict"
                        )
                    }
                }

                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 1
                    color: Qt.rgba(0.32, 0.62, 0.92, 0.12)
                }

                RowLayout {
                    Layout.fillWidth: true

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 2

                        Text {
                            text: "Allow foreground escalation"
                            color: "#DDEAFF"
                            font.pixelSize: 14
                            font.weight: Font.DemiBold
                        }

                        Text {
                            text: "Let Cua bring a target forward when background delivery is unavailable."
                            color: "#7188A7"
                            font.pixelSize: 11
                        }
                    }

                    AuroraSwitch {
                        checked: settingsModel.allowForeground
                        onToggled: settingsModel.setAllowForeground(checked)
                    }
                }
            }
        }

        AuroraCard {
            Layout.fillWidth: true
            Layout.preferredHeight: 182

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 20
                spacing: 12

                RowLayout {
                    Layout.fillWidth: true

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 2

                        Text {
                            text: "Confirm consequential actions"
                            color: "#DDEAFF"
                            font.pixelSize: 14
                            font.weight: Font.DemiBold
                        }

                        Text {
                            text: "Optional policy gate. Leave it off if you want the current direct Jev behavior."
                            color: "#7188A7"
                            font.pixelSize: 11
                        }
                    }

                    AuroraSwitch {
                        checked: settingsModel.confirmActions
                        onToggled: settingsModel.setConfirmActions(checked)
                    }
                }

                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 1
                    color: Qt.rgba(0.32, 0.62, 0.92, 0.12)
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 6

                    Text {
                        text: "Approved download root"
                        color: "#DDEAFF"
                        font.pixelSize: 14
                        font.weight: Font.DemiBold
                    }

                    TextField {
                        Layout.fillWidth: true
                        text: settingsModel.downloadRoot
                        placeholderText: "~/Downloads"
                        color: "#E9F4FF"
                        onEditingFinished: settingsModel.setDownloadRoot(text)
                    }
                }
            }
        }

        AuroraCard {
            Layout.fillWidth: true
            Layout.fillHeight: true
            glassOpacity: 0.48

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 20
                spacing: 12

                RowLayout {
                    Layout.fillWidth: true

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 2

                        Text {
                            text: "Start Porter when I sign in"
                            color: "#DDEAFF"
                            font.pixelSize: 14
                            font.weight: Font.DemiBold
                        }

                        Text {
                            text: "Uses your desktop session autostart so the resident assistant and top-bar icon are available after login."
                            color: "#7188A7"
                            font.pixelSize: 11
                            wrapMode: Text.Wrap
                            Layout.fillWidth: true
                        }
                    }

                    AuroraSwitch {
                        checked: settingsModel.startAtLogin
                        onToggled: settingsModel.setStartAtLogin(checked)
                    }
                }

                Item { Layout.fillHeight: true }

                Text {
                    text: settingsModel.applyStatus
                    color: settingsModel.applyStatus.indexOf("Could not") === 0
                        ? "#FF8497"
                        : "#64BFFF"
                    font.pixelSize: 11
                }
            }
        }
    }
}
