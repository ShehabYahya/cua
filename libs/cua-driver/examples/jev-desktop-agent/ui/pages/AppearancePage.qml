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
                    text: "Appearance"
                    color: "#F2F7FF"
                    font.pixelSize: 30
                    font.weight: Font.DemiBold
                }

                Text {
                    text: "Aurora Dark stays the base design; these controls tune its presence."
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
            Layout.preferredHeight: 190

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 20
                spacing: 12

                Text {
                    text: "Accent"
                    color: "#DDEAFF"
                    font.pixelSize: 15
                    font.weight: Font.DemiBold
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 12

                    Repeater {
                        model: ["#49A7FF", "#6B7CFF", "#4EDCC8", "#A675FF"]

                        delegate: Rectangle {
                            required property string modelData
                            Layout.preferredWidth: 44
                            Layout.preferredHeight: 44
                            radius: 12
                            color: modelData
                            border.width: settingsModel.accentColor === modelData ? 3 : 1
                            border.color: settingsModel.accentColor === modelData
                                ? "#F5FAFF"
                                : Qt.rgba(1, 1, 1, 0.18)

                            TapHandler {
                                onTapped: settingsModel.setAccentColor(modelData)
                            }
                        }
                    }

                    Item { Layout.fillWidth: true }

                    TextField {
                        Layout.preferredWidth: 150
                        text: settingsModel.accentColor
                        color: "#E9F4FF"
                        onEditingFinished: settingsModel.setAccentColor(text)
                    }
                }

                RowLayout {
                    Layout.fillWidth: true

                    Text {
                        text: "Animations"
                        color: "#D6E7FA"
                        font.pixelSize: 13
                        font.weight: Font.DemiBold
                    }

                    Item { Layout.fillWidth: true }

                    AuroraSwitch {
                        checked: settingsModel.animationsEnabled
                        onToggled: settingsModel.setAnimationsEnabled(checked)
                    }
                }
            }
        }

        AuroraCard {
            Layout.fillWidth: true
            Layout.preferredHeight: 210

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 20
                spacing: 12

                Text {
                    text: "Compact bar transparency"
                    color: "#DDEAFF"
                    font.pixelSize: 15
                    font.weight: Font.DemiBold
                }

                Text {
                    text: "Idle"
                    color: "#7188A7"
                    font.pixelSize: 11
                }

                AuroraSlider {
                    Layout.fillWidth: true
                    from: 0.08
                    to: 0.60
                    stepSize: 0.02
                    value: settingsModel.compactIdleOpacity
                    onPressedChanged: {
                        if (!pressed)
                            settingsModel.setCompactIdleOpacity(value)
                    }
                }

                Text {
                    text: "Hover"
                    color: "#7188A7"
                    font.pixelSize: 11
                }

                AuroraSlider {
                    Layout.fillWidth: true
                    from: 0.40
                    to: 0.98
                    stepSize: 0.02
                    value: settingsModel.compactHoverOpacity
                    onPressedChanged: {
                        if (!pressed)
                            settingsModel.setCompactHoverOpacity(value)
                    }
                }
            }
        }

        Item { Layout.fillHeight: true }
    }
}
