import QtQuick
import QtQuick.Controls

ComboBox {
    id: root
    property color accent: typeof settingsModel !== "undefined" ? settingsModel.accentColor : "#49A7FF"

    implicitHeight: 42
    leftPadding: 12
    rightPadding: 34
    focusPolicy: Qt.StrongFocus

    delegate: ItemDelegate {
        required property var modelData
        required property int index
        width: root.width
        highlighted: root.highlightedIndex === index

        contentItem: Text {
            text: root.textRole ? modelData[root.textRole] : modelData
            color: highlighted ? "#FFFFFF" : "#C9DAEC"
            font.pixelSize: 13
            verticalAlignment: Text.AlignVCenter
        }

        background: Rectangle {
            color: highlighted
                ? Qt.rgba(root.accent.r, root.accent.g, root.accent.b, 0.34)
                : "transparent"
        }
    }

    indicator: Text {
        x: root.width - width - 12
        y: (root.height - height) / 2 - 1
        text: root.popup.visible ? "⌃" : "⌄"
        color: root.enabled ? "#8FB2D5" : "#56677C"
        font.pixelSize: 16
    }

    contentItem: Text {
        leftPadding: 2
        rightPadding: root.indicator.width + root.spacing
        text: root.displayText
        color: root.enabled ? "#DFECFA" : "#67778D"
        font.pixelSize: 13
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }

    background: Rectangle {
        radius: 10
        color: Qt.rgba(0.045, 0.10, 0.18, 0.94)
        border.width: root.activeFocus ? 1 : 1
        border.color: root.activeFocus
            ? root.accent
            : Qt.rgba(0.38, 0.66, 0.92, 0.25)
    }

    popup: Popup {
        y: root.height + 4
        width: root.width
        implicitHeight: contentItem.implicitHeight + 8
        padding: 4

        contentItem: ListView {
            clip: true
            implicitHeight: contentHeight
            model: root.popup.visible ? root.delegateModel : null
            currentIndex: root.highlightedIndex
        }

        background: Rectangle {
            radius: 10
            color: "#0B192C"
            border.width: 1
            border.color: Qt.rgba(root.accent.r, root.accent.g, root.accent.b, 0.32)
        }
    }
}
