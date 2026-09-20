import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

Item {
    id: root

    Dialog {
        id: installDialog
        modal: true
        anchors.centerIn: parent
        title: "Install Cua Driver?"
        standardButtons: Dialog.Ok | Dialog.Cancel

        contentItem: Text {
            width: 480
            text: "Porter will run Cua's official Linux installer from cua.ai. This modifies software on your computer. Continue only if you want to install Cua Driver now."
            color: "#C8D7EC"
            font.pixelSize: 13
            wrapMode: Text.Wrap
        }

        onAccepted: maintenance.installDriver()
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 18

        Text {
            text: "About & Updates"
            color: "#F2F7FF"
            font.pixelSize: 30
            font.weight: Font.DemiBold
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 14

            AuroraCard {
                Layout.fillWidth: true
                Layout.preferredHeight: 190

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 20
                    spacing: 10

                    RowLayout {
                        Layout.fillWidth: true

                        PorterRing {
                            Layout.preferredWidth: 44
                            Layout.preferredHeight: 44
                            state: maintenance.updateAvailable
                                ? "attention"
                                : "ready"
                            accent: settingsModel.accentColor
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 2

                            Text {
                                text: "Porter " + maintenance.currentVersion
                                color: "#EEF7FF"
                                font.pixelSize: 18
                                font.weight: Font.DemiBold
                            }

                            Text {
                                text: "Native Aurora Dark desktop assistant"
                                color: "#7188A7"
                                font.pixelSize: 11
                            }
                        }
                    }

                    Text {
                        Layout.fillWidth: true
                        text: maintenance.updateStatus
                        color: maintenance.updateAvailable
                            ? "#FFBE67"
                            : "#77BFFF"
                        font.pixelSize: 12
                        wrapMode: Text.Wrap
                    }

                    RowLayout {
                        Layout.fillWidth: true

                        AuroraButton {
                            text: maintenance.updateChecking
                                ? "Checking…"
                                : "Check for updates"
                            enabled: !maintenance.updateChecking
                            onClicked: maintenance.checkPorterUpdates()
                        }

                        AuroraButton {
                            text: maintenance.updateAvailable
                                ? "Open update"
                                : "Releases"
                            onClicked: maintenance.openReleasePage()
                        }
                    }
                }
            }

            AuroraCard {
                Layout.fillWidth: true
                Layout.preferredHeight: 190

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 20
                    spacing: 9

                    Text {
                        text: "Cua Driver"
                        color: "#DDEAFF"
                        font.pixelSize: 16
                        font.weight: Font.DemiBold
                    }

                    Text {
                        text: maintenance.driverInstalled
                            ? maintenance.driverStatus
                            : "Not installed"
                        color: maintenance.driverInstalled
                            ? "#70D7B0"
                            : "#FFBE67"
                        font.pixelSize: 14
                        font.weight: Font.DemiBold
                    }

                    Text {
                        Layout.fillWidth: true
                        text: maintenance.driverVersion.length
                            ? maintenance.driverVersion
                            : maintenance.driverPath.length
                                ? maintenance.driverPath
                                : "Porter needs Cua Driver to control the desktop."
                        color: "#7188A7"
                        font.pixelSize: 11
                        wrapMode: Text.Wrap
                    }

                    RowLayout {
                        Layout.fillWidth: true

                        AuroraButton {
                            text: "Refresh"
                            enabled: !maintenance.driverBusy
                            onClicked: maintenance.refreshDriver()
                        }

                        AuroraButton {
                            text: "Doctor"
                            enabled: maintenance.driverInstalled
                                && !maintenance.driverBusy
                            onClicked: maintenance.doctorDriver()
                        }
                    }
                }
            }
        }

        AuroraCard {
            Layout.fillWidth: true
            Layout.preferredHeight: 150
            glassOpacity: 0.54

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 18
                spacing: 10

                RowLayout {
                    Layout.fillWidth: true

                    Text {
                        text: "Driver maintenance"
                        color: "#DDEAFF"
                        font.pixelSize: 15
                        font.weight: Font.DemiBold
                    }

                    Item { Layout.fillWidth: true }

                    AuroraButton {
                        text: "Official install guide"
                        onClicked: maintenance.openDriverDocs()
                    }
                }

                Text {
                    Layout.fillWidth: true
                    text: maintenance.driverInstalled
                        ? "Update uses Cua Driver's own updater. Porter never replaces the Driver with a bundled copy."
                        : "Porter can launch Cua's official installer after confirmation, or you can use the official installation guide."
                    color: "#7188A7"
                    font.pixelSize: 11
                    wrapMode: Text.Wrap
                }

                RowLayout {
                    Layout.fillWidth: true

                    AuroraButton {
                        text: maintenance.driverBusy
                            ? "Working…"
                            : maintenance.driverInstalled
                                ? "Update Cua Driver"
                                : "Install Cua Driver"
                        primary: true
                        enabled: !maintenance.driverBusy
                        onClicked: {
                            if (maintenance.driverInstalled)
                                maintenance.updateDriver()
                            else
                                installDialog.open()
                        }
                    }

                    AuroraButton {
                        text: "Cancel"
                        visible: maintenance.driverBusy
                        onClicked: maintenance.cancelDriverOperation()
                    }

                    Item { Layout.fillWidth: true }
                }
            }
        }

        AuroraCard {
            Layout.fillWidth: true
            Layout.fillHeight: true
            glassOpacity: 0.46

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 18
                spacing: 8

                Text {
                    text: "Driver output"
                    color: "#DDEAFF"
                    font.pixelSize: 14
                    font.weight: Font.DemiBold
                }

                ScrollView {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true

                    TextArea {
                        readOnly: true
                        text: maintenance.driverOutput.length
                            ? maintenance.driverOutput
                            : "Run Driver doctor, update, or installation to see output here."
                        color: "#AFC3DB"
                        font.family: "monospace"
                        font.pixelSize: 11
                        wrapMode: TextEdit.Wrap
                        background: Rectangle {
                            radius: 12
                            color: Qt.rgba(0.02, 0.05, 0.10, 0.62)
                            border.width: 1
                            border.color: Qt.rgba(0.30, 0.62, 0.92, 0.12)
                        }
                    }
                }
            }
        }
    }
}
