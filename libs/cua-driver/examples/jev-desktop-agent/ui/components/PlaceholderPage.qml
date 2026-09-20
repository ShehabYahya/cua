import QtQuick
import QtQuick.Layouts

Item {
    id: root
    property string pageTitle: ""
    property string pageDescription: ""

    ColumnLayout {
        anchors.fill: parent
        spacing: 20

        Text {
            text: root.pageTitle
            color: "#F2F7FF"
            font.pixelSize: 30
            font.weight: Font.DemiBold
        }

        AuroraCard {
            Layout.fillWidth: true
            Layout.preferredHeight: 170

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 22
                spacing: 10

                Text {
                    text: root.pageDescription
                    color: "#8CA3BF"
                    font.pixelSize: 14
                    wrapMode: Text.Wrap
                    Layout.fillWidth: true
                }

                Item { Layout.fillHeight: true }

                Text {
                    text: "Native settings page scaffold"
                    color: "#4A9FE0"
                    font.pixelSize: 12
                }
            }
        }

        Item { Layout.fillHeight: true }
    }
}
