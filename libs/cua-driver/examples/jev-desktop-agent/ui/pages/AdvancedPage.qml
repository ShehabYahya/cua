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
                    text: "Advanced & Diagnostics"
                    color: "#F2F7FF"
                    font.pixelSize: 30
                    font.weight: Font.DemiBold
                }

                Text {
                    text: "Inspect the live Cua/Porter stack and tune bounded execution budgets."
                    color: "#7F95B2"
                    font.pixelSize: 13
                }
            }

            AuroraButton {
                text: porter.diagnosticsRunning ? "Checking…" : "Refresh diagnostics"
                primary: true
                enabled: !porter.diagnosticsRunning
                onClicked: porter.refreshDiagnostics()
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 14

            AuroraCard {
                Layout.fillWidth: true
                Layout.preferredHeight: 150

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 18
                    spacing: 8

                    Text {
                        text: "Driver health"
                        color: "#7188A7"
                        font.pixelSize: 11
                        font.weight: Font.DemiBold
                    }

                    Text {
                        text: porter.diagnosticsStatus
                        color: porter.diagnosticsStatus === "Healthy"
                            ? "#70D7B0"
                            : porter.diagnosticsStatus === "Needs attention"
                                ? "#FFBE67"
                                : "#DCEAFF"
                        font.pixelSize: 18
                        font.weight: Font.DemiBold
                    }

                    Text {
                        Layout.fillWidth: true
                        text: porter.diagnosticsSummary
                        color: "#7188A7"
                        font.pixelSize: 12
                        wrapMode: Text.Wrap
                    }
                }
            }

            AuroraCard {
                Layout.fillWidth: true
                Layout.preferredHeight: 150

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 18
                    spacing: 10

                    Text {
                        text: "Execution budget"
                        color: "#7188A7"
                        font.pixelSize: 11
                        font.weight: Font.DemiBold
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        Text {
                            text: "Max steps"
                            color: "#DCEAFF"
                            font.pixelSize: 12
                        }
                        SpinBox {
                            from: 1
                            to: 200
                            value: settingsModel.maxSteps
                            onValueModified: settingsModel.setMaxSteps(value)
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        Text {
                            text: "Jev candidates"
                            color: "#DCEAFF"
                            font.pixelSize: 12
                        }
                        SpinBox {
                            from: 4
                            to: 32
                            value: settingsModel.maxCandidates
                            onValueModified: settingsModel.setMaxCandidates(value)
                        }
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
                anchors.margins: 18
                spacing: 10

                RowLayout {
                    Layout.fillWidth: true

                    Text {
                        text: "Live diagnostics"
                        color: "#DDEAFF"
                        font.pixelSize: 15
                        font.weight: Font.DemiBold
                    }

                    Item { Layout.fillWidth: true }

                    AuroraButton {
                        text: "Revert"
                        enabled: settingsModel.dirty
                        onClicked: settingsModel.revert()
                    }

                    AuroraButton {
                        text: "Apply"
                        primary: true
                        enabled: settingsModel.dirty
                        onClicked: settingsModel.apply()
                    }
                }

                ScrollView {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true

                    TextArea {
                        text: porter.diagnosticsDetails.length
                            ? porter.diagnosticsDetails
                            : "Run diagnostics to view Driver warnings, limitations, and capability flags."
                        readOnly: true
                        wrapMode: TextEdit.Wrap
                        color: "#AFC3DB"
                        font.family: "monospace"
                        font.pixelSize: 11
                        background: Rectangle {
                            radius: 12
                            color: Qt.rgba(0.02, 0.05, 0.10, 0.62)
                            border.width: 1
                            border.color: Qt.rgba(0.30, 0.62, 0.92, 0.12)
                        }
                    }
                }

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
