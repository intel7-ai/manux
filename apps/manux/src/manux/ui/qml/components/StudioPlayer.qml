import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../theme" as Theme

ColumnLayout {
    id: player
    required property var studio
    property bool showPose: false
    property real previewHeight: 280
    property real zoom: 1
    readonly property real aspect: studio.selected.frame_aspect || 0.5625
    spacing: Theme.Studio.gap
    onVisibleChanged: if (!visible && studio) studio.pause()
    component Copy: Label {
        color: Theme.Studio.ink; font.family: Theme.Studio.font
        font.pixelSize: Theme.Studio.body; wrapMode: Text.Wrap
    }
    Rectangle {
        Layout.fillWidth: true
        Layout.preferredHeight: player.previewHeight
        Layout.minimumHeight: player.previewHeight
        color: Theme.Studio.stage; radius: Theme.Studio.radius
        Canvas {
            id: skeleton
            objectName: "studioSkeleton"
            anchors.fill: parent; anchors.margins: Theme.Studio.pad
            anchors.topMargin: 36; anchors.bottomMargin: 32
            // Fit the recorded image rectangle: normalized x/y are not square pixels.
            readonly property real drawWidth: Math.min(width, height / player.aspect)
            readonly property real drawHeight: drawWidth * player.aspect
            readonly property real offsetX: (width - drawWidth) / 2
            readonly property real offsetY: (height - drawHeight) / 2
            onAvailableChanged: if (available) requestPaint()
            onVisibleChanged: if (visible) requestPaint()
            onWidthChanged: requestPaint()
            onHeightChanged: requestPaint()
            Connections { target: player.studio; function onFrameChanged() { skeleton.requestPaint() } }
            Connections {
                target: player
                function onShowPoseChanged() { skeleton.requestPaint() }
                function onAspectChanged() { skeleton.requestPaint() }
                function onZoomChanged() { skeleton.requestPaint() }
            }
            onPaint: {
                if (!available) return
                let c = getContext("2d"); c.reset()
                let f = player.studio.frame, lm = f.lm || [], pose = f.pose || []
                let w = drawWidth, h = drawHeight
                c.translate(offsetX, offsetY)
                c.beginPath(); c.rect(0,0,w,h); c.clip()
                c.strokeStyle = Theme.Studio.stageGrid; c.lineWidth = 1
                c.strokeRect(0,0,w,h)
                for (let n = 1; n < 3; n++) {
                    c.beginPath(); c.moveTo(n*w/3, 0); c.lineTo(n*w/3, h); c.stroke()
                    c.beginPath(); c.moveTo(0, n*h/3); c.lineTo(w, n*h/3); c.stroke()
                }
                if (f.zone >= 0) {
                    c.fillStyle = Qt.rgba(0.27, 0.9, 0.74, 0.09)
                    c.fillRect((f.zone%3)*w/3, Math.floor(f.zone/3)*h/3, w/3, h/3)
                }
                if (player.zoom > 1 && lm.length === 21) {
                    // Display only: keep one uniform scale, centered on the measured hand.
                    // Camera-position view remains available at 1x; never change recorded points.
                    let cx=0, cy=0
                    for (let i=0; i<lm.length; i++) { cx+=lm[i][0]/21; cy+=lm[i][1]/21 }
                    c.translate(w/2, h/2); c.scale(player.zoom, player.zoom); c.translate(-cx*w, -cy*h)
                }
                function line(points, color) {
                    c.strokeStyle = color; c.lineWidth = 3/player.zoom; c.lineCap = "round"; c.lineJoin = "round"
                    c.beginPath()
                    for (let k=0; k<points.length; k++) {
                        let p = points[k]; if (k===0) c.moveTo(p[0]*w,p[1]*h); else c.lineTo(p[0]*w,p[1]*h)
                    }
                    c.stroke()
                }
                if (player.showPose && pose.length === 6) {
                    line([pose[0],pose[1]], Theme.Studio.stageText)
                    line([pose[0],pose[2],pose[4]], Theme.Studio.mint)
                    line([pose[1],pose[3],pose[5]], Theme.Studio.mint)
                }
                if (lm.length === 21) {
                    let chains = [[0,1,2,3,4],[0,5,6,7,8],[0,9,10,11,12],[0,13,14,15,16],[0,17,18,19,20],[5,9,13,17]]
                    let colors = ["#FFD166", "#46E5BD", "#70CFFF", "#C4A0FF", "#FF9AAF", "#E7F2F4"]
                    for (let a=0;a<chains.length;a++) line(chains[a].map(function(i){return lm[i]}),colors[a])
                    c.fillStyle = Theme.Studio.stageText
                    for (let i=0;i<lm.length;i++) { c.beginPath(); c.arc(lm[i][0]*w,lm[i][1]*h,4/player.zoom,0,Math.PI*2); c.fill() }
                }
            }
        }
        Copy {
            anchors { left: parent.left; top: parent.top; margins: Theme.Studio.gap }
            text: player.zoom===1 ? "손 관절 21점 · 카메라 위치 · 녹화 비율 유지" : "손 중심 " + player.zoom + "배 확대 · 위치 확인은 전체 보기"; color: Theme.Studio.stageText
        }
        Copy {
            anchors { left: parent.left; bottom: parent.bottom; margins: Theme.Studio.gap }
            text: player.showPose ? ((player.studio.frame.pose || []).length ? "기록된 팔·어깨 · " + Number(player.studio.frame.pose_t || 0).toFixed(2) + "초" : "이 구간에는 팔·어깨 기록이 없습니다") : "엄지 노랑 · 검지 초록 · 중지 파랑 · 약지 보라 · 새끼 분홍"
            color: Theme.Studio.stageText
        }
        Copy {
            anchors.centerIn: parent; visible: !(player.studio.frame.lm || []).length
            text: player.studio.frameCount ? (player.studio.handFrameCount ? "이 구간은 손이 기록되지 않았습니다\n‘다음 손 프레임’으로 이동하세요" : "이 녹화에는 손 관절 기록이 없습니다") : "녹화를 선택하면 관절과 타임라인을 확인할 수 있어요"
            color: Theme.Studio.stageText; width: parent.width - 40; horizontalAlignment: Text.AlignHCenter
        }
    }
    Slider {
        id: scrubber; objectName: "studioScrubber"
        Layout.fillWidth: true; from: 0; to: Math.max(1, player.studio.frameCount-1)
        value: player.studio.frameIndex; stepSize: 1; enabled: player.studio.frameCount > 0
        Accessible.name: "녹화 프레임 탐색"
        onMoved: player.studio.seek(Math.round(value))
        background: Rectangle { x: scrubber.leftPadding; y: scrubber.topPadding + scrubber.availableHeight/2-2; width: scrubber.availableWidth; height: 4; radius: 2; color: Theme.Studio.line
            Rectangle { width: scrubber.visualPosition*parent.width; height: parent.height; radius: 2; color: Theme.Studio.mintDark } }
        handle: Rectangle { x: scrubber.leftPadding + scrubber.visualPosition*(scrubber.availableWidth-width); y: scrubber.topPadding+scrubber.availableHeight/2-height/2; width: 16; height: 16; radius: 8; color: Theme.Studio.mintDark }
    }
    Flow {
        Layout.fillWidth: true; spacing: 6
        StudioButton { objectName: "studioPlay"; text: player.studio.playing ? "일시정지" : "재생"; primary: true; enabled: player.studio.frameCount > 0 && !player.studio.busy; onClicked: player.studio.togglePlayback() }
        StudioButton { text: "‹"; Accessible.name: "이전 프레임"; onClicked: player.studio.seek(player.studio.frameIndex-1) }
        StudioButton { text: "›"; Accessible.name: "다음 프레임"; onClicked: player.studio.seek(player.studio.frameIndex+1) }
        StudioButton { text: player.studio.playbackSpeed + "×"; Accessible.name: "재생 속도"; onClicked: player.studio.setPlaybackSpeed(player.studio.playbackSpeed===0.5 ? 1 : player.studio.playbackSpeed===1 ? 2 : 0.5) }
        StudioButton { objectName: "studioNextHand"; text: "다음 손 프레임"; enabled: player.studio.handFrameCount>0; onClicked: player.studio.nextHand() }
        StudioButton { text: "다음 이벤트"; enabled: player.studio.timeline.length>0; onClicked: player.studio.nextEvent() }
        StudioButton { text: "팔·어깨"; primary: player.showPose; onClicked: player.showPose=!player.showPose }
        StudioButton { objectName: "studioZoom"; text: player.zoom===1 ? "손 확대" : player.zoom===2 ? "4배 확대" : "전체 보기"; primary: player.zoom>1; onClicked: player.zoom=player.zoom===1 ? 2 : player.zoom===2 ? 4 : 1 }
    }
    Copy { Layout.fillWidth: true; text: (Number(player.studio.frame.t || 0).toFixed(2)) + "초  ·  " + (player.studio.frameCount ? player.studio.frameIndex+1 : 0) + "/" + player.studio.frameCount + " 프레임  ·  " + ((player.studio.frame.label || {}).target || "—") + " / " + ((player.studio.frame.label || {}).phase || "—"); color: Theme.Studio.muted }
    Copy { Layout.fillWidth: true; text: "손 기록 " + player.studio.handFrameCount + " / " + player.studio.frameCount + "프레임 · 빈 구간은 보간하지 않고 그대로 표시합니다."; color: Theme.Studio.muted }
    Copy {
        Layout.fillWidth: true; visible: !!player.studio.simulationReport.counts
        text: "규칙: " + (player.studio.frame.rules_intents || []).join(", ") + "\n현재 정책: " + (player.studio.frame.model_intents || []).join(", ") + "\nTCN 연습 적용: " + (player.studio.frame.preview_intents || []).join(", ")
    }
}
