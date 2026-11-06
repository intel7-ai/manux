import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../theme" as Theme

ColumnLayout {
    id: player
    required property var studio
    property bool playing: false
    property real speed: 1
    spacing: Theme.Studio.gap
    onVisibleChanged: if (!visible) playing = false
    component Copy: Label {
        color: Theme.Studio.ink; font.family: Theme.Studio.font
        font.pixelSize: Theme.Studio.body; wrapMode: Text.Wrap
    }
    Rectangle {
        Layout.fillWidth: true; Layout.fillHeight: true
        Layout.minimumHeight: 130
        color: Theme.Studio.stage; radius: Theme.Studio.radius
        Canvas {
            id: skeleton
            objectName: "studioSkeleton"
            anchors.fill: parent; anchors.margins: Theme.Studio.pad
            onWidthChanged: requestPaint()
            onHeightChanged: requestPaint()
            Connections { target: player.studio; function onFrameChanged() { skeleton.requestPaint() } }
            onPaint: {
                let c = getContext("2d"); c.reset()
                let f = player.studio.frame, lm = f.lm || [], pose = f.pose || []
                let w = width, h = height
                c.strokeStyle = Theme.Studio.stageGrid; c.lineWidth = 1
                for (let n = 1; n < 3; n++) {
                    c.beginPath(); c.moveTo(n*w/3, 0); c.lineTo(n*w/3, h); c.stroke()
                    c.beginPath(); c.moveTo(0, n*h/3); c.lineTo(w, n*h/3); c.stroke()
                }
                if (f.zone >= 0) {
                    c.fillStyle = Qt.rgba(0.27, 0.9, 0.74, 0.09)
                    c.fillRect((f.zone%3)*w/3, Math.floor(f.zone/3)*h/3, w/3, h/3)
                }
                function line(points, color, dash) {
                    c.strokeStyle = color; c.lineWidth = 3; c.setLineDash(dash || [])
                    c.beginPath()
                    for (let k=0; k<points.length; k++) {
                        let p = points[k]; if (k===0) c.moveTo(p[0]*w,p[1]*h); else c.lineTo(p[0]*w,p[1]*h)
                    }
                    c.stroke(); c.setLineDash([])
                }
                if (pose.length === 6) {
                    line([pose[0],pose[1]], Theme.Studio.stageText)
                    line([pose[0],pose[2],pose[4]], Theme.Studio.mint)
                    line([pose[1],pose[3],pose[5]], Theme.Studio.mint)
                } else if (lm.length === 21) {
                    // Display-only estimate, never recorded or supplied to a model.
                    let wrist=lm[0], elbow=[wrist[0]*0.6+0.2, Math.min(0.95,wrist[1]+0.22)]
                    line([[0.5,0.96],elbow,wrist], Theme.Studio.muted,[5,5])
                }
                if (lm.length === 21) {
                    let chains = [[0,1,2,3,4],[0,5,6,7,8],[5,9,10,11,12],[9,13,14,15,16],[13,17,18,19,20],[0,17]]
                    for (let a=0;a<chains.length;a++) line(chains[a].map(function(i){return lm[i]}),Theme.Studio.mint)
                    c.fillStyle = Theme.Studio.stageText
                    for (let i=0;i<lm.length;i++) { c.beginPath(); c.arc(lm[i][0]*w,lm[i][1]*h,3,0,Math.PI*2); c.fill() }
                }
            }
        }
        Copy {
            anchors { left: parent.left; top: parent.top; margins: Theme.Studio.gap }
            text: "관절 재생 · 영상 미저장"; color: Theme.Studio.stageText
        }
        Copy {
            anchors { left: parent.left; bottom: parent.bottom; margins: Theme.Studio.gap }
            text: !(player.studio.frame.pose || []).length ? "팔·어깨는 추정 표시" : "기록된 팔·어깨 · " + Number(player.studio.frame.pose_t || 0).toFixed(2) + "초"
            color: Theme.Studio.stageText
        }
        Copy {
            anchors.centerIn: parent; visible: !(player.studio.frame.lm || []).length
            text: player.studio.frameCount ? "이 프레임에는 손 관절이 없습니다" : "녹화를 선택하면 관절과 타임라인을 확인할 수 있어요"
            color: Theme.Studio.stageText; width: parent.width - 40; horizontalAlignment: Text.AlignHCenter
        }
    }
    Slider {
        id: scrubber; objectName: "studioScrubber"
        Layout.fillWidth: true; from: 0; to: Math.max(1, player.studio.frameCount-1)
        value: player.studio.frameIndex; stepSize: 1; enabled: player.studio.frameCount > 0
        Accessible.name: "녹화 프레임 탐색"
        onMoved: { player.playing=false; player.studio.seek(Math.round(value)) }
        background: Rectangle { x: scrubber.leftPadding; y: scrubber.topPadding + scrubber.availableHeight/2-2; width: scrubber.availableWidth; height: 4; radius: 2; color: Theme.Studio.line
            Rectangle { width: scrubber.visualPosition*parent.width; height: parent.height; radius: 2; color: Theme.Studio.mintDark } }
        handle: Rectangle { x: scrubber.leftPadding + scrubber.visualPosition*(scrubber.availableWidth-width); y: scrubber.topPadding+scrubber.availableHeight/2-height/2; width: 16; height: 16; radius: 8; color: Theme.Studio.mintDark }
    }
    Flow {
        Layout.fillWidth: true; spacing: 6
        StudioButton { text: player.playing ? "일시정지" : "재생"; primary: true; enabled: player.studio.frameCount > 0; onClicked: { if(player.studio.frameIndex>=player.studio.frameCount-1)player.studio.seek(0); player.playing=!player.playing } }
        StudioButton { text: "‹"; Accessible.name: "이전 프레임"; onClicked: {player.playing=false;player.studio.seek(player.studio.frameIndex-1)} }
        StudioButton { text: "›"; Accessible.name: "다음 프레임"; onClicked: {player.playing=false;player.studio.seek(player.studio.frameIndex+1)} }
        StudioButton { text: player.speed + "×"; Accessible.name: "재생 속도"; onClicked: player.speed = player.speed===0.5 ? 1 : player.speed===1 ? 2 : 0.5 }
        StudioButton { text: "다음 이벤트"; enabled: player.studio.timeline.length>0; onClicked: {player.playing=false;player.studio.nextEvent()} }
    }
    Copy { Layout.fillWidth: true; text: (Number(player.studio.frame.t || 0).toFixed(2)) + "초  ·  " + (player.studio.frameCount ? player.studio.frameIndex+1 : 0) + "/" + player.studio.frameCount + " 프레임  ·  " + ((player.studio.frame.label || {}).target || "—") + " / " + ((player.studio.frame.label || {}).phase || "—"); color: Theme.Studio.muted }
    Copy {
        Layout.fillWidth: true; visible: !!player.studio.simulationReport.counts
        text: "규칙: " + (player.studio.frame.rules_intents || []).join(", ") + "\n현재 정책: " + (player.studio.frame.model_intents || []).join(", ") + "\nTCN 연습 적용: " + (player.studio.frame.preview_intents || []).join(", ")
    }
    Timer {
        interval: Math.max(1, player.studio.frameDelay/player.speed); repeat: true; running: player.playing && player.visible
        onTriggered: {
            if (player.studio.frameIndex >= player.studio.frameCount-1) player.playing=false
            else player.studio.seek(player.studio.frameIndex+1)
        }
    }
}
