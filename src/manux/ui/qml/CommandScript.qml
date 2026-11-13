import QtQuick
import QtQuick.Controls
import "ai_theme" as Theme

ScrollView {
    id: root
    property string value: ""
    property bool editable: false
    signal edited(string text)
    implicitHeight: 112
    contentWidth: availableWidth
    clip: true
    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
    TextArea {
        objectName: "commandScriptText"
        text: root.value
        textFormat: TextEdit.PlainText
        readOnly: !root.editable
        selectByMouse: true
        wrapMode: TextEdit.WrapAnywhere
        font.family: "Consolas"
        font.pixelSize: 12
        color: Theme.Studio.ink
        padding: 8
        placeholderText: root.editable ? "예: Get-Process | Sort-Object CPU -Descending | Select-Object -First 5" : ""
        background: Rectangle { color: Theme.Studio.paper; radius: 4; border.color: Theme.Studio.line }
        onTextChanged: if (root.editable && text !== root.value) root.edited(text)
    }
}
