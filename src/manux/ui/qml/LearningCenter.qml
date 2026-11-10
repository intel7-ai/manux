import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "components"
import "theme" as Theme

Popup {
    id: studio
    objectName: "trainingCenter"
    required property var controller
    readonly property var library: controller.learningStudio
    property string profileDraft: ""
    property int pageIndex: 0
    property int recordingSubTab: 0
    property string detailKey: ""
    property string search: ""
    property string confirmAction: "trash"
    property string confirmVersion: ""
    readonly property bool idle: !controller.recActive && !controller.trainingRunning && !controller.hubRunning
                                 && controller.trainingBusy === "" && !library.busy
    readonly property var detail: controller.trainingRows.find(function(r) {return r.key===detailKey}) || controller.trainingRows[0] || ({})
    signal recordingRequested()
    signal progressRequested()
    anchors.centerIn: parent
    width: Math.min(1240, parent.width-24); height: Math.min(900, parent.height-24)
    modal: true; focus: true; padding: Theme.Studio.pad; closePolicy: Popup.CloseOnEscape
    onPageIndexChanged: {if(controller)controller.setPoseStudioVisible(opened && pageIndex===2)}
    onOpened: {controller.setPoseStudioVisible(pageIndex===2); profileDraft=controller.trainingProfile; controller.openTraining();library.open() }
    onClosed: {library.hide();controller.closeTraining()}
    background: Rectangle { radius: Theme.Studio.radius; color: Theme.Studio.surface; border.color: Theme.Studio.line }
    component Copy: Label {
        color: Theme.Studio.ink; font.family: Theme.Studio.font; font.pixelSize: Theme.Studio.body; wrapMode: Text.Wrap
    }
    component Panel: Rectangle { color: Theme.Studio.paper; border.color: Theme.Studio.line; radius: Theme.Studio.radius }
    contentItem: ColumnLayout {
        spacing: Theme.Studio.gap
        RowLayout {
            Layout.fillWidth: true; Layout.minimumHeight: implicitHeight
            ColumnLayout {
                Layout.fillWidth: true; spacing: 2
                Copy { text: "학습 센터"; font.pixelSize: Theme.Studio.title; font.bold: true }
                Copy { text: "내 동작을 모으고, 확인하고, 검증 후 적용하세요"; color: Theme.Studio.muted }
            }
            Item { Layout.fillWidth: true }
            StudioButton { objectName: "openTrainingProgress"; text: "학습 진행"; primary: studio.controller.trainingRunning; onClicked: {studio.close();studio.progressRequested()} }
            StudioButton { text: "새로고침"; enabled: studio.idle; onClicked: {studio.controller.refreshTraining();studio.library.refresh()} }
            StudioButton { text: "닫기"; onClicked: studio.close() }
        }
        Panel {
            Layout.fillWidth: true; implicitHeight: identityRow.implicitHeight + Theme.Studio.gap*2; Layout.minimumHeight:implicitHeight
            RowLayout {
                id: identityRow
                anchors { fill: parent; margins: Theme.Studio.gap }
                spacing: 8
                Copy { text: "내 별명"; font.bold: true }
                StudioField {
                    id: alias; objectName: "trainingAlias"
                    Layout.fillWidth: true; Layout.minimumWidth: 90; Layout.maximumWidth: 220
                    maximumLength: 32; text: studio.profileDraft; onTextEdited: studio.profileDraft=text
                    placeholderText: "영문·숫자·_·-"; enabled: studio.idle; Accessible.name: "녹화 별명"
                    onAccepted: studio.controller.saveTrainingProfile(text)
                }
                StudioButton { objectName: "saveTrainingAlias"; text: studio.controller.trainingProfile===studio.profileDraft && studio.profileDraft!=="" ? "저장됨" : "별명 저장"; enabled: studio.idle; onClicked: studio.controller.saveTrainingProfile(alias.text) }
                Item { Layout.fillWidth: true }
                StudioButton { text: "학습용"; primary: studio.controller.recordingSplit==="train"; enabled: studio.idle; onClicked: studio.controller.setRecordingSplit("train") }
                StudioButton { text: "별도 평가"; primary: studio.controller.recordingSplit==="eval"; enabled: studio.idle; onClicked: studio.controller.setRecordingSplit("eval") }
            }
        }
        Panel {
            Layout.fillWidth: true; implicitHeight: firstUse.implicitHeight + Theme.Studio.gap * 2; Layout.minimumHeight: implicitHeight
            ColumnLayout {
                id: firstUse
                anchors { left: parent.left; right: parent.right; top: parent.top; margins: Theme.Studio.gap }
                spacing: 3
                Copy { Layout.fillWidth: true; font.bold: true; text: "처음이라면 ① 별명 저장 → ② 동작 선택 → ③ 녹화 → ④ 모델 학습 → ⑤ 적용" }
                Copy { Layout.fillWidth: true; font.pixelSize: Theme.Studio.small; color: Theme.Studio.muted; text: "데이터 녹화 탭: 손동작을 녹화하고 라이브러리를 확인합니다. 모델 관리 탭: 학습 및 모델을 적용합니다. 포즈 표본 탭: 단일 자세 표본을 수집합니다." }
            }
        }
        RowLayout {
            Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumHeight: 0; Layout.preferredHeight: 0; spacing: Theme.Studio.gap; clip:true
            ScrollView {
                id: navigationScroll
                objectName: "learningNavigationScroll"
                Layout.preferredWidth: studio.width < 900 ? 104 : Theme.Studio.navWidth
                Layout.minimumWidth: studio.width < 900 ? 104 : Theme.Studio.navWidth
                Layout.maximumWidth: Theme.Studio.navWidth
                Layout.fillHeight: true; Layout.minimumHeight: 0
                contentWidth: availableWidth; clip: true
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                ScrollBar.vertical.policy: ScrollBar.AsNeeded
                ColumnLayout {
                    width: navigationScroll.availableWidth; spacing: 6
                    Repeater {
                        model: ["데이터 녹화", "모델 관리", "포즈 표본"]
                        StudioButton {
                            required property string modelData; required property int index
                            objectName: "studioTab_" + index
                            Layout.fillWidth: true; text: modelData; primary: studio.pageIndex===index
                            onClicked: studio.pageIndex=index
                        }
                    }
                    Copy { Layout.fillWidth: true; text: studio.controller.trainingStorage; font.pixelSize: Theme.Studio.small; color: Theme.Studio.muted }
                    Copy { Layout.fillWidth: true; visible: studio.height>620; text: "평가는 학습과 다른 시도로 녹화하세요. 센터에서는 PC 제어를 멈춥니다."; font.pixelSize: Theme.Studio.small; color: Theme.Studio.muted }
                }
            }
            StackLayout {
                Layout.fillWidth: true; Layout.minimumWidth: 0; Layout.fillHeight: true; Layout.minimumHeight: 0; currentIndex: studio.pageIndex
                ColumnLayout {
                    spacing: Theme.Studio.gap
                    RowLayout {
                        Layout.fillWidth: true; spacing: 8
                        StudioButton {
                            text: "동작 선택 및 녹화"
                            primary: studio.recordingSubTab === 0
                            onClicked: studio.recordingSubTab = 0
                        }
                        StudioButton {
                            text: "녹화 라이브러리 (" + studio.library.recordings.length + ")"
                            primary: studio.recordingSubTab === 1
                            onClicked: studio.recordingSubTab = 1
                        }
                        Item { Layout.fillWidth: true }
                    }
                    StackLayout {
                        Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumHeight: 0
                        currentIndex: studio.recordingSubTab
                        ColumnLayout {
                            spacing: Theme.Studio.gap
                            Flow {
                                Layout.fillWidth: true; spacing: 6
                                Repeater {
                                    model: studio.controller.trainingCategories
                                    StudioButton { required property string modelData; text: modelData; primary: studio.controller.trainingFilter===modelData; onClicked: studio.controller.setTrainingFilter(modelData) }
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                StudioField { Layout.fillWidth: true; placeholderText: "동작 이름 검색"; Accessible.name: "동작 검색"; onTextEdited: studio.search=text }
                                StudioButton { text: "모두 선택"; onClicked: tasks.model.forEach(function(r){studio.controller.setTrainingSelected(r.key,true)}) }
                                StudioButton { text: "해제"; onClicked: tasks.model.forEach(function(r){studio.controller.setTrainingSelected(r.key,false)}) }
                            }
                            StudioButton { objectName: "selectRecordedTraining"; text: "녹화된 학습 지원 동작 선택"; enabled: studio.idle; onClicked: studio.controller.selectRecordedTraining() }
                            RowLayout {
                                Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumHeight: 0; spacing: Theme.Studio.gap
                                ListView {
                                    id: tasks; objectName: "trainingTasks"
                                    Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumHeight: 0; Layout.minimumWidth: 120
                                    clip: true; spacing: 6; model: studio.controller.trainingRows.filter(function(r){return !studio.search || (r.name+" "+r.instruction).toLowerCase().indexOf(studio.search.toLowerCase())>=0})
                                    ScrollBar.vertical: ScrollBar {}
                                    delegate: Rectangle {
                                        id: task; required property var modelData
                                        width: tasks.width; height: taskContent.implicitHeight+20
                                        radius: Theme.Studio.radius
                                        color: modelData.selected ? Theme.Studio.mintWash : Theme.Studio.paper
                                        border.color: modelData.selected ? Theme.Studio.mintDark : Theme.Studio.line
                                        TapHandler { onTapped: studio.detailKey=task.modelData.key }
                                        RowLayout {
                                            id: taskContent; anchors {left:parent.left;right:parent.right;top:parent.top;margins:10}
                                            StudioCheck { objectName: "taskCheck_"+task.modelData.key; checked: task.modelData.selected; Accessible.name: task.modelData.name+" 선택"; onClicked: {studio.detailKey=task.modelData.key;studio.controller.setTrainingSelected(task.modelData.key,checked)} }
                                            ColumnLayout {
                                                Layout.fillWidth: true; spacing: 3
                                                Copy { Layout.fillWidth: true; text: task.modelData.name; font.bold: true }
                                                Copy { Layout.fillWidth: true; text: task.modelData.data_text; font.pixelSize: Theme.Studio.small; color: Theme.Studio.muted }
                                                Copy { Layout.fillWidth: true; text: task.modelData.can_train ? "검증 후 모델 적용" : task.modelData.label==="none" ? "공통 중립 데이터" : "녹화·학습 지원"; font.pixelSize: Theme.Studio.small; color: Theme.Studio.mintDark }
                                            }
                                        }
                                    }
                                }
                                Panel {
                                    Layout.preferredWidth: 250; Layout.minimumWidth: 170; Layout.maximumWidth: 290; Layout.fillHeight: true; Layout.minimumHeight: 0
                                    ScrollView {
                                        id: detailScroll
                                        anchors {fill:parent;margins:Theme.Studio.pad} contentWidth: availableWidth; clip: true
                                        ColumnLayout {
                                            width: detailScroll.availableWidth; spacing: Theme.Studio.gap
                                            Copy { text: "동작 안내"; color: Theme.Studio.mintDark; font.bold: true }
                                            Copy { Layout.fillWidth: true; text: studio.detail.name || "동작을 선택하세요"; font.pixelSize: Theme.Studio.title; font.bold: true }
                                            Copy { Layout.fillWidth: true; text: studio.detail.instruction || "" }
                                            Rectangle { Layout.fillWidth: true; height: 1; color: Theme.Studio.line }
                                            Copy { Layout.fillWidth: true; text: studio.detail.data_text || "" }
                                            Copy { Layout.fillWidth: true; text: studio.detail.score_text || "" }
                                            Copy { Layout.fillWidth: true; text: studio.detail.advice || ""; color: Theme.Studio.muted }
                                            StudioButton { objectName: "focusRecord_"+(studio.detail.key || ""); text: "집중 녹화 설정"; enabled: studio.idle && !!studio.detail.key; onClicked: {if(studio.controller.focusRecord(studio.detail.key)){studio.close();studio.recordingRequested()}} }
                                            StudioButton { objectName: "trainGesture_"+(studio.detail.key || ""); text: "이 동작만 학습"; enabled: studio.idle && !!studio.detail.can_train && Number(studio.detail.reps || 0)>0; onClicked: studio.controller.trainSelectedGesture(studio.detail.key) }
                                            Copy { Layout.fillWidth: true; text: "체크한 항목이 녹화·학습 대상입니다."; color: Theme.Studio.muted }
                                        }
                                    }
                                }
                            }
                        }
                        RowLayout {
                            spacing: Theme.Studio.gap
                            ColumnLayout {
                                Layout.preferredWidth: Math.max(180, studio.width*0.23); Layout.fillHeight: true; Layout.minimumHeight: 0
                                Rectangle {
                                    Layout.fillWidth: true; Layout.preferredHeight: Math.max(75, Math.min(140, studio.height * .2))
                                    color: Theme.Studio.stage; radius: Theme.Studio.radius; clip: true
                                    Image {
                                        anchors.fill: parent; source: studio.controller.previewUrl; cache: false; fillMode: Image.PreserveAspectFit
                                        HandSkeleton { anchors.fill: parent; bridge: studio.controller; imageWidth: parent.paintedWidth; imageHeight: parent.paintedHeight }
                                    }
                                }
                                Copy { text: "녹화 라이브러리 · " + studio.library.recordings.length; font.bold: true }
                                ListView {
                                    id: recordings; objectName: "studioRecordings"
                                    Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumHeight: 0; clip: true; spacing: 6
                                    model: studio.library.recordings; ScrollBar.vertical: ScrollBar {}
                                    delegate: Panel {
                                        id: recording; required property var modelData
                                        width: recordings.width; height: recBody.implicitHeight+20
                                        color: studio.library.selectedKey===modelData.key ? Theme.Studio.mintWash : Theme.Studio.paper
                                        border.color: studio.library.selectedKey===modelData.key ? Theme.Studio.mintDark : Theme.Studio.line
                                        TapHandler { enabled: !studio.library.busy; onTapped: studio.library.selectRecording(recording.modelData.key) }
                                        ColumnLayout {
                                            id: recBody; anchors {left:parent.left;right:parent.right;top:parent.top;margins:10} spacing: 4
                                            Copy { Layout.fillWidth: true; text: recording.modelData.name; font.bold: true; font.pixelSize: Theme.Studio.small }
                                            Copy { Layout.fillWidth: true; text: recording.modelData.profile+" · "+recording.modelData.split+" · "+Math.round(recording.modelData.seconds)+"초"; color: Theme.Studio.muted; font.pixelSize: Theme.Studio.small }
                                            Copy { Layout.fillWidth: true; text: ({pass:"양호",warning:"주의",fail:"검수 필요"}[recording.modelData.quality.status] || "검사 전")+" · "+(recording.modelData.split==="eval" ? "별도 평가" : recording.modelData.included ? "학습 포함" : "학습 제외")+" · "+({original:"원본",clean:"보정본",augment:"증강본"}[recording.modelData.derived] || "원본"); color: recording.modelData.quality.status==="fail" ? Theme.Studio.danger : Theme.Studio.mintDark; font.pixelSize: Theme.Studio.small }
                                        }
                                    }
                                    Copy { anchors.centerIn: parent; width: parent.width; visible: recordings.count===0; text: studio.height < 620 ? "녹화 없음" : "완료된 녹화가 없습니다.\n동작을 선택해 녹화를 시작하세요."; color: Theme.Studio.muted }
                                }
                            }
                            ScrollView {
                                id: recordingDetail
                                objectName: "recordingDetailScroll"
                                contentHeight: recordingDetailBody.implicitHeight
                                ScrollBar.vertical.policy: ScrollBar.AlwaysOn
                                Layout.fillWidth: true; Layout.minimumWidth: 0; Layout.fillHeight: true; Layout.minimumHeight: 0
                                contentWidth: availableWidth; clip: true
                                ColumnLayout {
                                    id: recordingDetailBody
                                    width: recordingDetail.availableWidth; spacing: Theme.Studio.gap
                                    StudioPlayer { Layout.fillWidth: true; Layout.minimumHeight: implicitHeight; studio: studio.library; previewHeight: Math.max(110, Math.min(220, recordingDetail.availableHeight * .48)) }
                                    Copy { Layout.fillWidth: true; text: (studio.library.selected.quality || {}).summary || "관절 품질을 확인한 뒤 학습 데이터를 선택하세요."; color: Theme.Studio.muted }
                                    Flow {
                                        Layout.fillWidth: true; spacing: 6
                                        StudioButton { objectName: "studioInclude"; text: studio.library.selected.included ? "학습에서 제외" : "학습에 포함"; enabled: studio.idle && studio.library.selected.split==="train" && studio.library.selected.complete===true; onClicked: studio.library.include(!studio.library.selected.included) }
                                        StudioButton { text: "보정본 만들기"; enabled: studio.idle && studio.library.selected.split==="train" && studio.library.selected.derived==="original"; onClicked: studio.library.transform("clean") }
                                        StudioButton { text: "증강본 만들기"; enabled: studio.idle && studio.library.selected.split==="train" && studio.library.selected.derived==="original"; onClicked: studio.library.transform("augment") }
                                        StudioButton { text: "휴지통으로"; enabled: studio.idle && studio.library.selectedKey!==""; onClicked: {studio.confirmAction="trash";confirmation.open()} }
                                    }
                                    Copy { Layout.fillWidth: true; text: "품질은 관절의 기계적 검사입니다. 손짓 정답 여부는 직접 확인하세요."; font.pixelSize: Theme.Studio.small; color: Theme.Studio.muted }
                                    Repeater {
                                        model: (studio.library.selected.quality || {}).issues || []
                                        StudioButton { required property var modelData; Layout.fillWidth: true; flat: true; text: Number(modelData.start_t).toFixed(2)+"초 · "+modelData.message; onClicked: studio.library.seekTime(modelData.start_t) }
                                    }
                                }
                            }
                        }
                    }
                }
                ModelLibrary { Layout.fillWidth:true;Layout.fillHeight:true;Layout.minimumHeight:0;controller:studio.controller;library:studio.library }
                PoseSnapshots { Layout.fillWidth:true;Layout.fillHeight:true;Layout.minimumHeight:0;controller:studio.controller }
            }
        }
        Panel {
            Layout.fillWidth: true; visible:studio.pageIndex<2; implicitHeight: footer.implicitHeight+Theme.Studio.gap*2
            ColumnLayout {
                id: footer; visible:studio.pageIndex<2; anchors {left:parent.left;right:parent.right;top:parent.top;margins:Theme.Studio.gap} spacing: 6
                RowLayout {
                    Layout.fillWidth: true
                    Copy { Layout.fillWidth: true; Layout.minimumWidth: 0; text: studio.controller.selectedTrainingKeys.length+"개 선택 · "+(studio.controller.selectedTrainingRows.map(function(r){return r.name}).join(", ") || "체크한 동작이 여기에 표시됩니다"); maximumLineCount: 2; elide: Text.ElideRight; color: Theme.Studio.mintDark; font.bold: true }
                    StudioButton { objectName: "recordSelection"; text: "선택 동작 녹화"; primary: true; enabled: studio.idle && studio.controller.selectedTrainingKeys.length>0; onClicked: {if(studio.controller.recordTrainingSelection()){studio.close();studio.recordingRequested()}} }
                    StudioButton { objectName: "trainSelection"; text: "선택 동작만 학습"; enabled: studio.idle && studio.controller.selectionCanTrain; onClicked: studio.controller.trainTrainingSelection() }
                    StudioButton { text: "중단"; visible: studio.controller.trainingRunning || studio.library.busy; onClicked: {studio.controller.stopTraining();studio.library.cancel()} }
                }
                Copy { objectName: "recordSelectionMessage"; Layout.fillWidth: true; text: studio.controller.trainingNote; visible: text!==""; color: Theme.Studio.mintDark }
                Copy { Layout.fillWidth: true; visible: studio.controller.selectedTrainingKeys.length>0 && !studio.controller.selectionCanTrain; text: "학습 지원 동작을 하나 이상 선택하고 녹화 전용 항목의 체크를 해제하세요. 중립 데이터도 함께 학습합니다."; font.pixelSize: Theme.Studio.small; color: Theme.Studio.muted }
                Copy { Layout.fillWidth: true; text: studio.library.busy ? studio.library.status : studio.controller.trainingRunning ? studio.controller.trainingStage : studio.library.status; color: Theme.Studio.muted; maximumLineCount: 2; elide: Text.ElideRight }
                Rectangle { Layout.fillWidth: true; height: 4; radius: 2; color: Theme.Studio.line; visible: studio.controller.trainingRunning
                    Rectangle { width: parent.width*Math.max(0,Math.min(1,studio.controller.trainingProgress)); height: parent.height; radius: 2; color: Theme.Studio.mintDark } }
            }
        }
    }
    Popup {
        id: confirmation; objectName: "studioActionConfirm"
        function accept() {
            close()
            if(studio.confirmAction==="upload")studio.controller.runHub("upload")
            else if(studio.confirmAction==="trash")studio.library.transform("trash")
            else studio.library.restore(studio.confirmVersion)
        }
        anchors.centerIn: parent; width: Math.min(480,studio.width-40); padding: Theme.Studio.pad; modal: true; focus: true
        closePolicy: Popup.CloseOnEscape
        background: Rectangle { color: Theme.Studio.paper; radius: Theme.Studio.radius; border.color: Theme.Studio.line }
        contentItem: ColumnLayout {
            spacing: Theme.Studio.gap
            Copy { text: studio.confirmAction==="upload" ? "내 녹화 공유" : studio.confirmAction==="trash" ? "녹화를 휴지통으로 이동" : "현재 평가로 검증 후 복원"; font.bold: true; font.pixelSize: Theme.Studio.title }
            Copy { Layout.fillWidth: true; text: studio.confirmAction==="upload" ? studio.controller.trainingProfile+"의 완료된 원본 관절 기록을 "+studio.controller.hubRepo+"에 업로드합니다. 영상은 포함하지 않습니다." : studio.confirmAction==="trash" ? (studio.library.selected.name || "")+" 파일을 로컬 휴지통으로 옮깁니다. 이후 학습에서는 빠집니다." : "이 저장 버전을 현재 별도의 평가 시도로 다시 검사합니다. 기존 동작 회귀나 평가 부족이면 사용 중 모델을 유지합니다." }
            RowLayout {
                Layout.alignment: Qt.AlignRight
                StudioButton { text: "취소"; onClicked: confirmation.close() }
                StudioButton { text: "진행"; primary: true; enabled: studio.idle; onClicked: confirmation.accept() }
            }
        }
    }
    Popup {
        id: uploadConfirm; objectName: "uploadConfirm"
        function accept() { close(); studio.controller.runHub("upload") }
        anchors.centerIn: parent; width: Math.min(460, studio.width-24); padding: Theme.Studio.pad; modal: true; focus: true
        background: Rectangle { color: Theme.Studio.paper; radius: Theme.Studio.radius; border.color: Theme.Studio.line }
        contentItem: ColumnLayout {
            spacing: Theme.Studio.gap
            Copy { Layout.fillWidth: true; text: studio.controller.trainingProfile + "의 선택 동작 녹화를 팀 저장소에 업로드합니다." }
            RowLayout {
                Layout.alignment: Qt.AlignRight
                StudioButton { text: "취소"; onClicked: uploadConfirm.close() }
                StudioButton { objectName: "uploadRecordings"; text: "업로드"; primary: true; enabled: studio.idle && studio.controller.trainingProfile!=="" && studio.controller.hubCanUpload; onClicked: uploadConfirm.accept() }
            }
        }
    }
}
