import QtQuick
import QtQuick.Controls

Button {
    id: root
    property bool primary: false

    implicitHeight: 42
    leftPadding: 16
    rightPadding: 16

    contentItem: Text {
        text: root.text
        color: root.enabled ? (root.primary ? "#F7FBFF" : "#C8D7EC") : "#64748A"
        font.pixelSize: 14
        font.weight: Font.DemiBold
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
    }

    background: Rectangle {
        radius: 12
        color: {
            if (!root.enabled)
                return Qt.rgba(0.09, 0.14, 0.22, 0.45)
            if (root.down)
                return root.primary ? "#147DCE" : Qt.rgba(0.13, 0.22, 0.34, 0.95)
            if (root.hovered)
                return root.primary ? "#249AEE" : Qt.rgba(0.12, 0.20, 0.31, 0.92)
            return root.primary ? "#188AD9" : Qt.rgba(0.08, 0.14, 0.23, 0.80)
        }
        border.width: 1
        border.color: root.primary
            ? Qt.rgba(0.40, 0.76, 1.0, 0.55)
            : Qt.rgba(0.35, 0.64, 0.95, 0.14)

        Behavior on color { ColorAnimation { duration: 120 } }
        Behavior on border.color { ColorAnimation { duration: 120 } }
    }
}
