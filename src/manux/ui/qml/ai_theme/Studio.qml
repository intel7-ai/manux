pragma Singleton
import QtQuick
import "../theme" as MainTheme
QtObject {
    readonly property bool dark: MainTheme.Studio.dark
    readonly property color ink: MainTheme.Studio.ink
    readonly property color muted: MainTheme.Studio.muted
    readonly property color mint: MainTheme.Studio.mint
    readonly property color mintDark: MainTheme.Studio.mintDark
    readonly property color mintPressed: dark ? "#5FDCBA" : "#35CCA7"
    readonly property color mintWash: MainTheme.Studio.mintWash
    readonly property color surface: MainTheme.Studio.surface
    readonly property color paper: MainTheme.Studio.paper
    readonly property color line: MainTheme.Studio.line
    readonly property color hover: MainTheme.Studio.soft
    readonly property color amber: MainTheme.Studio.amber
    readonly property color amberWash: MainTheme.Studio.amberWash
    readonly property color mintText: MainTheme.Studio.mintText
    readonly property color dangerText: MainTheme.Studio.dangerText
    readonly property url logoSource: MainTheme.Studio.logoSource
    readonly property string font: MainTheme.Studio.font
    readonly property int gap: MainTheme.Studio.gap
    readonly property int pad: MainTheme.Studio.pad
    readonly property int radius: MainTheme.Studio.radius
    readonly property int body: MainTheme.Studio.body
    readonly property int small: MainTheme.Studio.small
    readonly property int title: MainTheme.Studio.title
    readonly property int navWidth: MainTheme.Studio.navWidth
    readonly property color danger: MainTheme.Studio.danger
    readonly property color dangerFill: MainTheme.Studio.dangerFill
    readonly property color dangerPressed: dark ? "#842F3C" : "#7F2733"
    readonly property color stage: MainTheme.Studio.stage
    readonly property color stageGrid: MainTheme.Studio.stageGrid
    readonly property color stageText: MainTheme.Studio.stageText

    // Each source token corresponds to one literal in the preserved upstream
    // dialog. The regression test reverses only these typed color substitutions.
    readonly property color sourceSurface: surface
    readonly property color sourcePaper: paper
    readonly property color sourceSetupError: dark ? "#F3A8AE" : "#B3261E"
    readonly property color sourceCaptureSurface: MainTheme.Studio.brandNavy
    readonly property color sourceOverlapWarning: dark ? "#F1C678" : "#B45309"
    readonly property color sourceRequestInfo: dark ? "#91D1EF" : "#0369A1"
    readonly property color sourceProposalError: MainTheme.Studio.dangerWash
    readonly property color sourceHistorySurface: "#0E1B26"
    readonly property color sourceBoneShadow: Qt.rgba(0, 0, 0, 0.55)
    readonly property color sourceJoint: "#FFFFFF"
    readonly property color sourcePreviewHighlight: "#F2B544"
}
