import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

Item {
    ColumnLayout {
        anchors.fill: parent
        spacing: 20

        Text {
            text: "Voice & Audio"
            color: "#F2F7FF"
            font.pixelSize: 30
            font.weight: Font.DemiBold
        }

        AuroraCard {
            Layout.fillWidth: true
            Layout.preferredHeight: 190

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 22
                spacing: 14

                RowLayout {
                    Layout.fillWidth: true

                    PorterRing {
                        Layout.preferredWidth: 42
                        Layout.preferredHeight: 42
                        state: porter.state
                        listening: porter.state === "listening"
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 2

                        Text {
                            text: porter.listening
                                ? "Hands-free listening is on"
                                : "Hands-free listening is off"
                            color: "#E6F2FF"
                            font.pixelSize: 17
                            font.weight: Font.DemiBold
                        }

                        Text {
                            text: porter.listening
                                ? "Speak normally. Porter detects speech and submits automatically when you stop."
                                : "Enable listening to control Porter without touching the keyboard or microphone button."
                            color: "#7890AE"
                            font.pixelSize: 12
                            wrapMode: Text.Wrap
                            Layout.fillWidth: true
                        }
                    }

                    AuroraButton {
                        text: porter.listening ? "Mute" : "Enable"
                        primary: !porter.listening
                        onClicked: porter.toggleListening()
                    }
                }

                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 9
                    radius: height / 2
                    color: Qt.rgba(0.17, 0.28, 0.42, 0.45)

                    Rectangle {
                        width: parent.width * porter.micLevel
                        height: parent.height
                        radius: height / 2
                        color: "#3FAEFF"

                        Behavior on width {
                            NumberAnimation { duration: 80 }
                        }
                    }
                }

                Text {
                    text: porter.state === "listening"
                        ? "Speech detected"
                        : porter.listening
                            ? "Waiting for speech"
                            : "Microphone muted"
                    color: porter.state === "listening" ? "#63C7FF" : "#69819E"
                    font.pixelSize: 12
                }
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
                    spacing: 9

                    Text {
                        text: "Automatic endpointing"
                        color: "#DCEAFF"
                        font.pixelSize: 15
                        font.weight: Font.DemiBold
                    }

                    Text {
                        text: Number(porter.voiceSilenceSeconds).toFixed(2)
                            + " s trailing silence"
                        color: "#6FC3FF"
                        font.pixelSize: 14
                    }

                    Text {
                        Layout.fillWidth: true
                        text: "Porter keeps listening while you speak and submits after the configured pause. No Enter key or microphone press is required."
                        color: "#7087A6"
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
                    spacing: 9

                    Text {
                        text: "Latest transcript"
                        color: "#DCEAFF"
                        font.pixelSize: 15
                        font.weight: Font.DemiBold
                    }

                    Text {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        text: porter.transcript.length
                            ? porter.transcript
                            : "No speech captured yet."
                        color: porter.transcript.length ? "#D6E8FA" : "#60748F"
                        font.pixelSize: 13
                        wrapMode: Text.Wrap
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
                spacing: 10

                Text {
                    text: "How it works"
                    color: "#DDEAFF"
                    font.pixelSize: 16
                    font.weight: Font.DemiBold
                }

                Text {
                    Layout.fillWidth: true
                    text: "Silence is processed locally and discarded. Once local VAD detects speech, Porter buffers the utterance, preserves a short pre-roll so fast speech is not clipped, detects the ending pause, transcribes that utterance, and sends the resulting command to the same resident PorterRuntime."
                    color: "#7188A7"
                    font.pixelSize: 13
                    wrapMode: Text.Wrap
                }

                Item { Layout.fillHeight: true }

                Text {
                    text: porter.listening ? "LIVE" : "MUTED"
                    color: porter.listening ? "#56B9F5" : "#6B7890"
                    font.pixelSize: 11
                    font.weight: Font.Bold
                    font.letterSpacing: 1.2
                }
            }
        }
    }
}
