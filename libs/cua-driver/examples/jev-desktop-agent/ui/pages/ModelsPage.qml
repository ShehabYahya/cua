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
                    text: "Models & Providers"
                    color: "#F2F7FF"
                    font.pixelSize: 30
                    font.weight: Font.DemiBold
                }

                Text {
                    text: "Choose how Porter reaches Jev and the optional writer, vision, and speech services."
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

            GridLayout {
                anchors.fill: parent
                anchors.margins: 20
                columns: 2
                columnSpacing: 26
                rowSpacing: 12

                Text {
                    text: "Jev provider"
                    color: "#DDEAFF"
                    font.pixelSize: 14
                    font.weight: Font.DemiBold
                }

                AuroraComboBox {
                    id: providerBox
                    Layout.fillWidth: true
                    model: ["Auto", "OpenRouter", "TypeSafe"]
                    currentIndex: settingsModel.provider === "openrouter"
                        ? 1
                        : settingsModel.provider === "typesafe" ? 2 : 0

                    onActivated: {
                        settingsModel.setProvider(
                            currentIndex === 1
                                ? "openrouter"
                                : currentIndex === 2 ? "typesafe" : "auto"
                        )
                    }
                }

                Text {
                    text: "Jev model"
                    color: "#DDEAFF"
                    font.pixelSize: 14
                    font.weight: Font.DemiBold
                }

                TextField {
                    Layout.fillWidth: true
                    text: settingsModel.jevModel
                    placeholderText: "~typesafe/jev-latest"
                    color: "#E9F4FF"
                    onEditingFinished: settingsModel.setJevModel(text)
                }

                Text {
                    text: "Vision"
                    color: "#DDEAFF"
                    font.pixelSize: 14
                    font.weight: Font.DemiBold
                }

                AuroraSwitch {
                    text: checked ? "Enabled" : "Disabled"
                    checked: settingsModel.visionEnabled
                    onToggled: settingsModel.setVisionEnabled(checked)
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 14

            AuroraCard {
                Layout.fillWidth: true
                Layout.preferredHeight: 164

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 18
                    spacing: 9

                    Text {
                        text: "Vision model"
                        color: "#DDEAFF"
                        font.pixelSize: 14
                        font.weight: Font.DemiBold
                    }

                    TextField {
                        Layout.fillWidth: true
                        text: settingsModel.visionModel
                        enabled: settingsModel.visionEnabled
                        color: "#E9F4FF"
                        onEditingFinished: settingsModel.setVisionModel(text)
                    }

                    Text {
                        text: "Used lazily only when semantic desktop state is insufficient."
                        color: "#6F86A4"
                        font.pixelSize: 11
                        wrapMode: Text.Wrap
                        Layout.fillWidth: true
                    }
                }
            }

            AuroraCard {
                Layout.fillWidth: true
                Layout.preferredHeight: 164

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 18
                    spacing: 9

                    Text {
                        text: "Writer model"
                        color: "#DDEAFF"
                        font.pixelSize: 14
                        font.weight: Font.DemiBold
                    }

                    TextField {
                        Layout.fillWidth: true
                        text: settingsModel.writerModel
                        color: "#E9F4FF"
                        onEditingFinished: settingsModel.setWriterModel(text)
                    }

                    Text {
                        text: "Used only when Porter needs generated text rather than literal text from your command."
                        color: "#6F86A4"
                        font.pixelSize: 11
                        wrapMode: Text.Wrap
                        Layout.fillWidth: true
                    }
                }
            }
        }

        AuroraCard {
            Layout.fillWidth: true
            Layout.fillHeight: true
            glassOpacity: 0.52

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 20
                spacing: 14

                Text {
                    text: "Credentials"
                    color: "#DDEAFF"
                    font.pixelSize: 16
                    font.weight: Font.DemiBold
                }

                Text {
                    text: "API keys are stored in your operating system credential store, not in Porter's QSettings file."
                    color: "#7087A6"
                    font.pixelSize: 12
                    wrapMode: Text.Wrap
                    Layout.fillWidth: true
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 10

                    TextField {
                        id: openRouterKey
                        Layout.fillWidth: true
                        placeholderText: settingsModel.openRouterConfigured
                            ? "OpenRouter key stored"
                            : "OpenRouter API key"
                        echoMode: TextInput.Password
                        color: "#E9F4FF"
                    }

                    AuroraButton {
                        text: "Save OpenRouter"
                        onClicked: {
                            settingsModel.saveOpenRouterKey(openRouterKey.text)
                            openRouterKey.text = ""
                        }
                    }

                    AuroraButton {
                        text: "Clear"
                        enabled: settingsModel.openRouterConfigured
                        onClicked: settingsModel.clearOpenRouterKey()
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 10

                    TextField {
                        id: typeSafeKey
                        Layout.fillWidth: true
                        placeholderText: settingsModel.typeSafeConfigured
                            ? "TypeSafe key stored"
                            : "TypeSafe API key"
                        echoMode: TextInput.Password
                        color: "#E9F4FF"
                    }

                    AuroraButton {
                        text: "Save TypeSafe"
                        onClicked: {
                            settingsModel.saveTypeSafeKey(typeSafeKey.text)
                            typeSafeKey.text = ""
                        }
                    }

                    AuroraButton {
                        text: "Clear"
                        enabled: settingsModel.typeSafeConfigured
                        onClicked: settingsModel.clearTypeSafeKey()
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
