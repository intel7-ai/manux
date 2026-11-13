pragma Singleton
import QtQuick
import QtCore as Core
QtObject {
    id: theme
    // Command appearance is independent of gesture, model and input settings.
    readonly property url settingsUrl: typeof commandThemeSettingsUrl !== "undefined"
        ? commandThemeSettingsUrl
        : Core.StandardPaths.writableLocation(Core.StandardPaths.GenericConfigLocation).toString().replace(/\/$/, "") + "/MANUX-Command/appearance.ini"
    readonly property QtObject appearance: Core.Settings {
        location: theme.settingsUrl
        category: "Appearance"
        property string selectedTheme: "light"
    }
    readonly property string themeName: appearance.selectedTheme === "dark" ? "dark" : "light"
    readonly property bool dark: themeName === "dark"
    function setTheme(name) {
        if (name !== "light" && name !== "dark") return
        appearance.selectedTheme = name
        appearance.setValue("selectedTheme", name)
        appearance.sync()
    }
    readonly property url logoSource: Qt.resolvedUrl(dark ? "../assets/manux-dark.svg" : "../assets/manux.svg")
    readonly property color brandNavy: "#112331"
    readonly property color ink: dark ? "#E5ECE9" : "#112331"
    readonly property color muted: dark ? "#AFBFC9" : "#596C73"
    readonly property color mint: "#46E5BD"
    readonly property color mintDark: dark ? "#72E3C4" : "#147D68"
    readonly property color mintWash: dark ? "#193D42" : "#E3F6EE"
    readonly property color surface: dark ? "#10232F" : "#F3F6F4"
    readonly property color paper: dark ? "#172D3B" : "#FFFFFF"
    readonly property color soft: dark ? "#203A47" : "#EDF2F3"
    readonly property color line: dark ? "#304C59" : "#DBE6E5"
    readonly property color primaryBackground: dark ? mint : brandNavy
    readonly property color primaryText: dark ? brandNavy : "#F5F7F3"
    readonly property color mintText: brandNavy
    readonly property color dangerText: "#FFFFFF"
    readonly property color rail: dark ? "#0B1B26" : brandNavy
    readonly property color railText: "#F5F7F3"
    readonly property color railMuted: "#AFBFC4"
    readonly property color amber: dark ? "#F0C06E" : "#9B690E"
    readonly property color amberWash: dark ? "#3D3427" : "#FFF6E4"
    readonly property string font: "Noto Sans KR"
    readonly property int gap: 12
    readonly property int pad: 18
    readonly property int radius: 8
    readonly property int body: 13
    readonly property int small: 11
    readonly property int title: 22
    readonly property int navWidth: 136
    readonly property color danger: dark ? "#FF97A4" : "#A93745"
    readonly property color dangerWash: dark ? "#432C36" : "#FDECEE"
    readonly property color dangerFill: "#A93745"
    readonly property color stage: "#142A38"
    readonly property color stageGrid: "#2E4754"
    readonly property color stageText: "#E7F2F4"
}
