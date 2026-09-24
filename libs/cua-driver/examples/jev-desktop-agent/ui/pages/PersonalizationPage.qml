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
                    text: "Personalization"
                    color: "#F2F7FF"
                    font.pixelSize: 30
                    font.weight: Font.DemiBold
                }

                Text {
                    text: "Keep Porter recognizable as Porter while tailoring the experience to you."
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
            Layout.preferredHeight: 180

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 20
                spacing: 10

                Text {
                    text: "What should Porter call you?"
                    color: "#DDEAFF"
                    font.pixelSize: 15
                    font.weight: Font.DemiBold
                }

                TextField {
                    Layout.fillWidth: true
                    text: settingsModel.preferredName
                    placeholderText: "Optional"
                    color: "#E9F4FF"
                    maximumLength: 80
                    onEditingFinished: settingsModel.setPreferredName(text)
                }

                Text {
                    text: "This currently personalizes the native shell only; it is not injected into Jev's desktop-control decisions."
                    color: "#6F86A4"
                    font.pixelSize: 11
                    wrapMode: Text.Wrap
                    Layout.fillWidth: true
                }
            }
        }

        AuroraCard {
            Layout.fillWidth: true
            Layout.preferredHeight: 150
            glassOpacity: 0.52

            RowLayout {
                anchors.fill: parent
                anchors.margins: 20
                spacing: 16

                PorterRing {
                    Layout.preferredWidth: 48
                    Layout.preferredHeight: 48
                    state: "ready"
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 4

                    Text {
                        text: settingsModel.preferredName.length
                            ? "Good to see you, " + settingsModel.preferredName
                            : "Good to see you"
                        color: "#F1F7FF"
                        font.pixelSize: 21
                        font.weight: Font.DemiBold
                    }

                    Text {
                        text: "Porter stays the product and assistant name."
                        color: "#738AA8"
                        font.pixelSize: 12
                    }
                }
            }
        }

        Item { Layout.fillHeight: true }
    }
}
