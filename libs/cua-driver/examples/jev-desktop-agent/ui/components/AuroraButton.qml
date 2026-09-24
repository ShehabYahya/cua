import QtQuick
import QtQuick.Controls

Button {
    id: root
    property bool primary: false
    property string tooltip: ""
    property string accessibleDescription: ""
    property color accent: typeof settingsModel !== "undefined" ? settingsModel.accentColor : "#188AD9"
    property bool animationsEnabled: typeof settingsModel !== "undefined" ? settingsModel.animationsEnabled : true

    implicitWidth: Math.max(88, contentItem.implicitWidth + leftPadding + rightPadding)
    implicitHeight: 42
    leftPadding: 16
    rightPadding: 16
    hoverEnabled: true
    focusPolicy: Qt.StrongFocus
    scale: down ? 0.975 : 1.0

    Accessible.role: Accessible.Button
    Accessible.name: text
    Accessible.description: accessibleDescription

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
                return root.primary ? Qt.darker(root.accent, 1.20) : Qt.rgba(0.13, 0.22, 0.34, 0.95)
            if (root.hovered)
                return root.primary ? Qt.lighter(root.accent, 1.12) : Qt.rgba(0.12, 0.20, 0.31, 0.92)
            return root.primary ? root.accent : Qt.rgba(0.08, 0.14, 0.23, 0.80)
        }
        border.width: 1
        border.color: root.activeFocus
            ? Qt.lighter(root.accent, 1.25)
            : root.primary
            ? Qt.rgba(root.accent.r, root.accent.g, root.accent.b, 0.55)
            : Qt.rgba(0.35, 0.64, 0.95, 0.14)

        Behavior on color { enabled: root.animationsEnabled; ColorAnimation { duration: 120 } }
        Behavior on border.color { enabled: root.animationsEnabled; ColorAnimation { duration: 120 } }
    }

    Behavior on scale {
        enabled: root.animationsEnabled
        NumberAnimation { duration: 90; easing.type: Easing.OutCubic }
    }

    ToolTip.visible: root.tooltip.length > 0 && root.hovered
    ToolTip.text: root.tooltip
    ToolTip.delay: 550
}
