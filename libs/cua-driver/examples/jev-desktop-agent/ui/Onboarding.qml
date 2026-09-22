import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "components"

Item {
    id: root
    anchors.fill: parent
    visible: settingsModel.needsOnboarding
    z: 1000

    Dialog {
        id: installDriverDialog
        modal: true
        anchors.centerIn: parent
        width: 520
        title: "Install Cua Driver?"
        standardButtons: Dialog.Ok | Dialog.Cancel

        contentItem: Text {
            width: installDriverDialog.availableWidth
            text: "Porter will run Cua's official Linux installer from cua.ai. This changes software on your computer. Continue?"
            color: "#C8D7EC"
            font.pixelSize: 13
            wrapMode: Text.Wrap
        }

        onAccepted: maintenance.installDriver()
    }

    Rectangle {
        anchors.fill: parent
        color: Qt.rgba(0.01, 0.025, 0.055, 0.90)
    }

    AuroraCard {
        width: Math.min(parent.width - 80, 720)
        height: Math.min(parent.height - 80, 610)
        anchors.centerIn: parent
        glassOpacity: 0.96

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 30
            spacing: 18

            RowLayout {
                Layout.fillWidth: true

                PorterRing {
                    Layout.preferredWidth: 52
                    Layout.preferredHeight: 52
                    state: porter.state
                    listening: false
                    accent: settingsModel.accentColor
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 2

                    Text {
                        text: "Welcome to Porter"
                        color: "#F4F9FF"
                        font.pixelSize: 27
                        font.weight: Font.DemiBold
                    }

                    Text {
                        text: "Connect the model provider, check desktop control, then Porter can stay resident."
                        color: "#8198B6"
                        font.pixelSize: 12
                    }
                }
            }

            AuroraCard {
                Layout.fillWidth: true
                Layout.preferredHeight: 215
                glassOpacity: 0.58

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 18
                    spacing: 10

                    Text {
                        text: "1  Model connection"
                        color: "#DCEAFF"
                        font.pixelSize: 15
                        font.weight: Font.DemiBold
                    }

                    AuroraComboBox {
                        id: providerBox
                        Layout.fillWidth: true
                        model: ["OpenRouter", "TypeSafe"]
                        currentIndex: settingsModel.provider === "typesafe" ? 1 : 0
                    }

                    TextField {
                        id: apiKey
                        Layout.fillWidth: true
                        echoMode: TextInput.Password
                        placeholderText: providerBox.currentIndex === 0
                            ? (settingsModel.openRouterConfigured
                                ? "OpenRouter key already stored"
                                : "OpenRouter API key")
                            : (settingsModel.typeSafeConfigured
                                ? "TypeSafe key already stored"
                                : "TypeSafe API key")
                        color: "#E9F4FF"
                    }

                    Text {
                        Layout.fillWidth: true
                        text: providerBox.currentIndex === 0
                            ? "OpenRouter also powers Porter's cloud speech transcription."
                            : "TypeSafe can run Jev decisions; hands-free speech still needs OpenRouter STT."
                        color: "#6F86A4"
                        font.pixelSize: 11
                        wrapMode: Text.Wrap
                    }
                }
            }

            AuroraCard {
                Layout.fillWidth: true
                Layout.preferredHeight: 170
                glassOpacity: 0.52

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 18
                    spacing: 9

                    RowLayout {
                        Layout.fillWidth: true

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 2

                            Text {
                                text: "2  Desktop control"
                                color: "#DCEAFF"
                                font.pixelSize: 15
                                font.weight: Font.DemiBold
                            }

                            Text {
                                text: maintenance.driverInstalled
                                    ? maintenance.driverStatus
                                    : "Cua Driver is not installed"
                                color: maintenance.driverInstalled
                                    ? "#70D7B0"
                                    : "#FFBE67"
                                font.pixelSize: 11
                            }
                        }

                        AuroraButton {
                            text: maintenance.driverBusy
                                ? "Working…"
                                : maintenance.driverInstalled
                                    ? "Driver doctor"
                                    : "Install Driver"
                            enabled: !maintenance.driverBusy
                            onClicked: {
                                if (maintenance.driverInstalled)
                                    maintenance.doctorDriver()
                                else
                                    installDriverDialog.open()
                            }
                        }
                    }

                    Text {
                        Layout.fillWidth: true
                        text: maintenance.driverInstalled
                            ? (porter.diagnosticsStatus + " — " + porter.diagnosticsSummary)
                            : "Porter uses Cua Driver as its desktop-control body. You can install it now or open the official guide from About & Updates."
                        color: "#7188A7"
                        font.pixelSize: 11
                        wrapMode: Text.Wrap
                    }

                    RowLayout {
                        Layout.fillWidth: true

                        AuroraButton {
                            text: porter.diagnosticsRunning
                                ? "Checking…"
                                : "Check Porter + Cua"
                            enabled: maintenance.driverInstalled
                                && !porter.diagnosticsRunning
                                && !maintenance.driverBusy
                            onClicked: porter.refreshDiagnostics()
                        }

                        AuroraButton {
                            text: "Install guide"
                            visible: !maintenance.driverInstalled
                            onClicked: maintenance.openDriverDocs()
                        }

                        Item { Layout.fillWidth: true }

                        Text {
                            text: porter.diagnosticsStatus
                            color: porter.diagnosticsStatus === "Healthy"
                                ? "#70D7B0"
                                : porter.diagnosticsStatus === "Needs attention"
                                    ? "#FFBE67"
                                    : "#8BA3C0"
                            font.pixelSize: 11
                            font.weight: Font.DemiBold
                        }
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true

                CheckBox {
                    id: voiceCheck
                    text: "Hands-free listening"
                    checked: settingsModel.handsFree
                }

                CheckBox {
                    id: loginCheck
                    text: "Start at login"
                    checked: settingsModel.startAtLogin
                }

                Item { Layout.fillWidth: true }
            }

            Item { Layout.fillHeight: true }

            RowLayout {
                Layout.fillWidth: true

                Text {
                    Layout.fillWidth: true
                    text: settingsModel.applyStatus
                    color: settingsModel.applyStatus.indexOf("Could not") === 0
                        ? "#FF8497"
                        : "#64BFFF"
                    font.pixelSize: 11
                    wrapMode: Text.Wrap
                }

                AuroraButton {
                    text: settingsModel.applying ? "Finishing…" : "Finish setup"
                    primary: true
                    enabled: !settingsModel.applying && (
                        apiKey.text.trim().length > 0
                        || (
                            providerBox.currentIndex === 0
                            && settingsModel.openRouterConfigured
                        )
                        || (
                            providerBox.currentIndex === 1
                            && settingsModel.typeSafeConfigured
                        )
                    )
                    onClicked: {
                        const key = apiKey.text.trim()
                        if (providerBox.currentIndex === 0) {
                            settingsModel.setProvider("openrouter")
                            if (key.length > 0)
                                settingsModel.saveOpenRouterKey(key)
                        } else {
                            settingsModel.setProvider("typesafe")
                            if (key.length > 0)
                                settingsModel.saveTypeSafeKey(key)
                        }
                        settingsModel.setHandsFree(voiceCheck.checked)
                        settingsModel.setStartAtLogin(loginCheck.checked)
                        settingsModel.setOnboardingComplete(true)
                        apiKey.text = ""
                        settingsModel.apply()
                    }
                }
            }
        }
    }
}
