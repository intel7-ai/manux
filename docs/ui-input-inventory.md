# v0.5 전체 UI 입력 핸들러 감사 목록

자동 정적 목록이다. 각 행은 소스 연결 검사이며 실제 버튼 실행 결과는 ui-mlp-audit.md에서 별도로 구분한다.

| 파일 | 줄 | 입력 핸들러 | Bridge 대상 |
| --- | --- | --- | --- |
| AdvancedSettings.qml | 97 | onClicked: advanced.changeValue(modelData.key, checked) | QML 내부/다른 뷰모델 |
| AdvancedSettings.qml | 104 | onActivated: advanced.changeValue(modelData.key, currentText) | QML 내부/다른 뷰모델 |
| AdvancedSettings.qml | 115 | onMoved: advanced.changeValue(modelData.key, Number(value.toFixed(6))) | QML 내부/다른 뷰모델 |
| AdvancedSettings.qml | 127 | onTextEdited: advanced.changeValue(modelData.key, text.trim() ? Number(text) : NaN) | QML 내부/다른 뷰모델 |
| AdvancedSettings.qml | 154 | onClicked: { | QML 내부/다른 뷰모델 |
| AdvancedSettings.qml | 165 | onClicked: advanced.controller.applyAdvancedSettings(advanced.edits) | applyAdvancedSettings |
| AdvancedSettings.qml | 167 | Button { text: "닫기"; onClicked: advanced.close() } | QML 내부/다른 뷰모델 |
| components/DatasetSharing.qml | 24 | StudioButton { objectName: "connectDatasetHub"; text: "연결과 권한 확인"; enabled: page.idle; onClicked: page.controller.runHub("status") } | runHub |
| components/DatasetSharing.qml | 30 | StudioButton { objectName: "uploadRecordings"; text: "내 데이터 업로드"; primary: true; enabled: page.idle && page.controller.trainingProfile!=="" && page.controller.hubCanUpload; onClicked: confirmation.open() } | QML 내부/다른 뷰모델 |
| components/DatasetSharing.qml | 31 | StudioButton { objectName: "downloadActionDatasets"; text: "팀 데이터 다운로드"; enabled: page.idle; onClicked: page.controller.runHub("download") } | runHub |
| components/DatasetSharing.qml | 37 | StudioButton { text: page.library.poseEnabled ? "팔·어깨 기록 켜짐" : "팔·어깨 기록 켜기"; primary: page.library.poseEnabled; enabled: page.idle; onClicked: page.library.togglePose() } | QML 내부/다른 뷰모델 |
| components/DatasetSharing.qml | 50 | StudioButton { text:"취소";onClicked:confirmation.close() } | QML 내부/다른 뷰모델 |
| components/DatasetSharing.qml | 51 | StudioButton { text:"업로드";primary:true;enabled:page.idle;onClicked:confirmation.accept() } | QML 내부/다른 뷰모델 |
| components/ModelLibrary.qml | 25 | StudioButton { text: "로컬 새로고침"; enabled: page.idle; onClicked: page.library.refresh() } | QML 내부/다른 뷰모델 |
| components/ModelLibrary.qml | 26 | StudioButton { text: "모델 파일 가져오기"; enabled: page.idle; onClicked: page.library.importModel() } | QML 내부/다른 뷰모델 |
| components/ModelLibrary.qml | 27 | StudioButton { objectName: "fetchRemoteModels"; text: "공유 모델 불러오기"; enabled: page.idle; onClicked: page.library.modelAction("remote", "", "") } | QML 내부/다른 뷰모델 |
| components/ModelLibrary.qml | 28 | StudioButton { text: "진단"; enabled: page.idle; onClicked: page.library.diagnose() } | QML 내부/다른 뷰모델 |
| components/ModelLibrary.qml | 43 | StudioButton { text: page.selectedId===modelRow.modelData.id ? "선택됨" : "이 모델 선택"; primary: page.selectedId===modelRow.modelData.id; onClicked: {page.selectedId=modelRow.modelData.id;page.draftName=modelRow.modelData.name} } | QML 내부/다른 뷰모델 |
| components/ModelLibrary.qml | 48 | StudioField { objectName: "modelDisplayName"; Layout.fillWidth: true; text: page.draftName; onTextEdited: page.draftName=text; maximumLength: 80; placeholderText: "모델 이름 · 예: 책상 오른손 v2"; Accessible.name: "모델 표시 이름"; enabled: page.selectedId!=="" && page.idle } | QML 내부/다른 뷰모델 |
| components/ModelLibrary.qml | 51 | StudioButton { text: "이름 저장"; enabled: page.idle && page.selectedId!=="" && page.draftName.trim()!==""; onClicked: page.library.modelAction("rename",page.selectedId,page.draftName) } | QML 내부/다른 뷰모델 |
| components/ModelLibrary.qml | 52 | StudioButton { objectName: "uploadNamedModel"; text: "모델 업로드"; enabled: page.idle && page.selectedId!=="" && page.draftName.trim()!==""; onClicked: {page.pendingId=page.selectedId;page.pendingAction="upload";confirmation.open()} } | QML 내부/다른 뷰모델 |
| components/ModelLibrary.qml | 53 | StudioButton { objectName: "previewNamedModel"; text: page.selectedModel.format==="spatial" ? "포즈 미리보기로 선택" : "이 모델로 시험"; enabled: page.idle && page.selectedId!==""; onClicked: page.library.modelAction("preview",page.selectedId,"") } | QML 내부/다른 뷰모델 |
| components/ModelLibrary.qml | 54 | StudioButton { objectName: "applyNamedModel"; text: "검증 후 이 모델 사용"; primary: true; visible: page.selectedModel.format!=="spatial"; enabled: page.idle && page.selectedId!==""; onClicked: {page.pendingId=page.selectedId;page.pendingAction="apply";confirmation.open()} } | QML 내부/다른 뷰모델 |
| components/ModelLibrary.qml | 65 | StudioButton { text: "다운로드"; enabled: page.idle; onClicked: page.library.modelAction("download",remoteRow.modelData.id,"") } | QML 내부/다른 뷰모델 |
| components/ModelLibrary.qml | 76 | StudioButton { text: "검증 후 복원"; enabled: page.idle && versionRow.modelData.valid && !versionRow.modelData.active; onClicked: {page.pendingId=versionRow.modelData.version;page.pendingAction="restore";confirmation.open()} } | QML 내부/다른 뷰모델 |
| components/ModelLibrary.qml | 90 | StudioButton {text:"취소";onClicked:confirmation.close()} | QML 내부/다른 뷰모델 |
| components/ModelLibrary.qml | 91 | StudioButton {text:"진행";primary:true;enabled:page.idle;onClicked:{confirmation.close();if(page.pendingAction==="restore")page.library.restore(page.pendingId);else page.library.modelAction(page.pendingAction,page.pendingId,page.draftName)}} | QML 내부/다른 뷰모델 |
| components/PoseSnapshots.qml | 45 | StudioButton {required property var modelData;text:modelData.name;primary:modelData.selected;enabled:page.idle;onClicked:page.controller.setStudioCategory(modelData.id)} | setStudioCategory |
| components/PoseSnapshots.qml | 52 | StudioButton { required property var modelData; width: Math.max(140,(page.availableWidth-8)/2); text: modelData.short+" · "+modelData.totalCount; primary: modelData.selected; enabled: page.idle; onClicked: page.controller.setStudioGroundTruth(modelData.id) } | setStudioGroundTruth |
| components/PoseSnapshots.qml | 59 | StudioButton { text:"연속 수집";primary:page.controller.studioCaptureMode==="stream";enabled:page.idle;onClicked:page.controller.setStudioCaptureMode("stream") } | setStudioCaptureMode |
| components/PoseSnapshots.qml | 60 | StudioButton { text:"한 장씩";primary:page.controller.studioCaptureMode==="snapshot";enabled:page.idle;onClicked:page.controller.setStudioCaptureMode("snapshot") } | setStudioCaptureMode |
| components/PoseSnapshots.qml | 61 | StudioCheck { text:"불일치 표본만";checked:page.controller.studioDiscrepancyOnly;enabled:page.idle;onClicked:page.controller.setStudioDiscrepancyOnly(checked) } | setStudioDiscrepancyOnly |
| components/PoseSnapshots.qml | 65 | StudioButton { objectName:"capturePoseSample";text:page.controller.studioStreaming ? "수집 정지" : page.controller.studioCaptureMode==="stream" ? "수집 시작" : "한 장 캡처";primary:true;enabled:page.idle;onClicked:page.controller.triggerStudioCapture() } | triggerStudioCapture |
| components/PoseSnapshots.qml | 66 | StudioButton { text:"마지막 표본 취소";enabled:page.idle;onClicked:page.controller.undoStudioSample() } | undoStudioSample |
| components/PoseSnapshots.qml | 67 | StudioButton { objectName:"savePoseSamples";text:"표본 저장";enabled:page.idle;onClicked:page.controller.saveStudioDataset() } | saveStudioDataset |
| components/PoseSnapshots.qml | 68 | StudioButton { text:"새 수집";enabled:page.idle;onClicked:page.controller.clearStudioSamples() } | clearStudioSamples |
| components/PoseSnapshots.qml | 69 | StudioButton { objectName:"trainPoseModel";text:"포즈 모델 학습";enabled:page.idle;onClicked:page.controller.trainStudioModel() } | trainStudioModel |
| components/PoseSnapshots.qml | 74 | StudioButton {text:page.controller.spatialGuardEnabled ? "실험 보조 차단 켜짐 · 끄기" : "실험 보조 차단 켜기";primary:page.controller.spatialGuardEnabled;enabled:page.idle && page.controller.studioModelLoaded;onClicked:page.controller.toggleSpatialGuard()} | toggleSpatialGuard |
| components/PoseSnapshots.qml | 78 | StudioButton {text:"이 포즈 표본 업로드";enabled:page.idle && page.controller.trainingProfile!=="";onClicked:shareConfirm.open()} | QML 내부/다른 뷰모델 |
| components/PoseSnapshots.qml | 79 | StudioButton {text:"이 포즈 표본 다운로드";enabled:page.idle;onClicked:page.controller.learningStudio.poseDatasetAction("download")} | QML 내부/다른 뷰모델 |
| components/PoseSnapshots.qml | 91 | StudioButton{text:"취소";onClicked:shareConfirm.close()} | QML 내부/다른 뷰모델 |
| components/PoseSnapshots.qml | 92 | StudioButton{text:"업로드";primary:true;enabled:page.idle;onClicked:{shareConfirm.close();page.controller.learningStudio.poseDatasetAction("upload")}} | QML 내부/다른 뷰모델 |
| components/StudioPlayer.qml | 111 | onMoved: player.studio.seek(Math.round(value)) | QML 내부/다른 뷰모델 |
| components/StudioPlayer.qml | 118 | StudioButton { objectName: "studioPlay"; text: player.studio.playing ? "일시정지" : "재생"; primary: true; enabled: player.studio.frameCount > 0 && !player.studio.busy; onClicked: player.studio.togglePlayback() } | QML 내부/다른 뷰모델 |
| components/StudioPlayer.qml | 119 | StudioButton { text: "‹"; Accessible.name: "이전 프레임"; onClicked: player.studio.seek(player.studio.frameIndex-1) } | QML 내부/다른 뷰모델 |
| components/StudioPlayer.qml | 120 | StudioButton { text: "›"; Accessible.name: "다음 프레임"; onClicked: player.studio.seek(player.studio.frameIndex+1) } | QML 내부/다른 뷰모델 |
| components/StudioPlayer.qml | 121 | StudioButton { text: player.studio.playbackSpeed + "×"; Accessible.name: "재생 속도"; onClicked: player.studio.setPlaybackSpeed(player.studio.playbackSpeed===0.5 ? 1 : player.studio.playbackSpeed===1 ? 2 : 0.5) } | QML 내부/다른 뷰모델 |
| components/StudioPlayer.qml | 122 | StudioButton { objectName: "studioNextHand"; text: "다음 손 프레임"; enabled: player.studio.handFrameCount>0; onClicked: player.studio.nextHand() } | QML 내부/다른 뷰모델 |
| components/StudioPlayer.qml | 123 | StudioButton { text: "다음 이벤트"; enabled: player.studio.timeline.length>0; onClicked: player.studio.nextEvent() } | QML 내부/다른 뷰모델 |
| components/StudioPlayer.qml | 124 | StudioButton { text: "팔·어깨"; primary: player.showPose; onClicked: player.showPose=!player.showPose } | QML 내부/다른 뷰모델 |
| components/StudioPlayer.qml | 125 | StudioButton { objectName: "studioZoom"; text: player.zoom===1 ? "손 확대" : player.zoom===2 ? "4배 확대" : "전체 보기"; primary: player.zoom>1; onClicked: player.zoom=player.zoom===1 ? 2 : player.zoom===2 ? 4 : 1 } | QML 내부/다른 뷰모델 |
| components/StudioStepper.qml | 22 | onClicked: control.edited(Math.max(control.from, control.value - control.stepSize)) | QML 내부/다른 뷰모델 |
| components/StudioStepper.qml | 35 | onClicked: control.edited(Math.min(control.to, control.value + control.stepSize)) | QML 내부/다른 뷰모델 |
| dashboard.qml | 72 | ActionButton { text: "닫기"; Layout.topMargin: 12; Layout.rightMargin: 12; onClicked: settingsDialog.close() } | QML 내부/다른 뷰모델 |
| dashboard.qml | 87 | onClicked: { settingsDialog.close(); dashboard.beginHandSetup() } | beginHandSetup |
| dashboard.qml | 99 | onMoved: dashboard.setPointerSensitivity(Math.round(value)) | setPointerSensitivity |
| dashboard.qml | 138 | onClicked: { capturing = true; shortcutError.text = ""; forceActiveFocus() } | QML 내부/다른 뷰모델 |
| dashboard.qml | 158 | ActionButton { text: "기본"; onClicked: dashboard.resetShortcut(shortcutAction) } | resetShortcut |
| dashboard.qml | 187 | ActionButton { Layout.fillWidth: true; text: "방금 이상했어요 (F)"; onClicked: dashboard.markFailure() } | markFailure |
| dashboard.qml | 188 | ActionButton { Layout.fillWidth: true; text: "로그 묶어 내보내기"; onClicked: dashboard.exportLogs() } | exportLogs |
| dashboard.qml | 202 | onClicked: { settingsDialog.close(); advancedSettings.open() } | QML 내부/다른 뷰모델 |
| dashboard.qml | 226 | onClicked: dashboard.toggleMotion() | toggleMotion |
| dashboard.qml | 232 | onClicked: dashboard.cycleProfile() | cycleProfile |
| dashboard.qml | 265 | onClicked: { settingsDialog.close(); dashboard.beginPointerCalibration() } | beginPointerCalibration |
| dashboard.qml | 270 | onClicked: dashboard.resetPointerCalibration() | resetPointerCalibration |
| dashboard.qml | 278 | onClicked: { settingsDialog.close(); dashboard.beginTrackingTest() } | beginTrackingTest |
| dashboard.qml | 297 | onClicked: { settingsDialog.close(); dashboard.command(111) } | command |
| dashboard.qml | 304 | Shortcut { sequence: "Return"; enabled: !window.learningDialogOpen && !dashboard.trackingActive && (dashboard.calibratingPointer); autoRepeat: false; onActivated: dashboard.capturePointerSample() } | capturePointerSample |
| dashboard.qml | 305 | Shortcut { sequence: "Enter"; enabled: !window.learningDialogOpen && !dashboard.trackingActive && (dashboard.calibratingPointer); autoRepeat: false; onActivated: dashboard.capturePointerSample() } | capturePointerSample |
| dashboard.qml | 306 | Shortcut { sequence: "Esc"; enabled: !window.learningDialogOpen && !dashboard.trackingActive; onActivated: dashboard.command(27) } | command |
| dashboard.qml | 307 | Shortcut { sequence: "Space"; enabled: !window.learningDialogOpen && !dashboard.trackingActive && (!settingsDialog.visible) && !newModelField.activeFocus; onActivated: dashboard.command(32) } | command |
| dashboard.qml | 308 | Shortcut { sequence: "Q"; enabled: !window.learningDialogOpen && !dashboard.trackingActive && !newModelField.activeFocus; onActivated: dashboard.command(81) } | command |
| dashboard.qml | 309 | Shortcut { sequence: "O"; enabled: !window.learningDialogOpen && !dashboard.trackingActive && !newModelField.activeFocus; onActivated: dashboard.command(111) } | command |
| dashboard.qml | 310 | Shortcut { sequence: "M"; enabled: !window.learningDialogOpen && !dashboard.trackingActive && !newModelField.activeFocus; onActivated: dashboard.command(109) } | command |
| dashboard.qml | 311 | Shortcut { sequence: "N"; enabled: !window.learningDialogOpen && !dashboard.trackingActive && !newModelField.activeFocus; onActivated: dashboard.command(110) } | command |
| dashboard.qml | 312 | Shortcut { sequence: "F"; enabled: !window.learningDialogOpen && !dashboard.trackingActive && !newModelField.activeFocus; onActivated: dashboard.markFailure() } | markFailure |
| dashboard.qml | 313 | Shortcut { sequence: "C"; enabled: !window.learningDialogOpen && !dashboard.trackingActive && !newModelField.activeFocus; onActivated: dashboard.command(99) } | command |
| dashboard.qml | 314 | Shortcut { sequence: "P"; enabled: !window.learningDialogOpen && !dashboard.trackingActive && !newModelField.activeFocus; onActivated: dashboard.command(112) } | command |
| dashboard.qml | 315 | Shortcut { sequence: "1"; enabled: !window.learningDialogOpen && dashboard.currentPage === "studio" && !newModelField.activeFocus; onActivated: dashboard.command(49) } | command |
| dashboard.qml | 316 | Shortcut { sequence: "2"; enabled: !window.learningDialogOpen && dashboard.currentPage === "studio" && !newModelField.activeFocus; onActivated: dashboard.command(50) } | command |
| dashboard.qml | 317 | Shortcut { sequence: "3"; enabled: !window.learningDialogOpen && dashboard.currentPage === "studio" && !newModelField.activeFocus; onActivated: dashboard.command(51) } | command |
| dashboard.qml | 318 | Shortcut { sequence: "4"; enabled: !window.learningDialogOpen && dashboard.currentPage === "studio" && !newModelField.activeFocus; onActivated: dashboard.command(52) } | command |
| dashboard.qml | 319 | Shortcut { sequence: "5"; enabled: !window.learningDialogOpen && dashboard.currentPage === "studio" && !newModelField.activeFocus; onActivated: dashboard.command(53) } | command |
| dashboard.qml | 320 | Shortcut { sequence: "6"; enabled: !window.learningDialogOpen && dashboard.currentPage === "studio" && !newModelField.activeFocus; onActivated: dashboard.command(54) } | command |
| dashboard.qml | 321 | Shortcut { sequence: "7"; enabled: !window.learningDialogOpen && dashboard.currentPage === "studio" && !newModelField.activeFocus; onActivated: dashboard.command(55) } | command |
| dashboard.qml | 322 | Shortcut { sequence: "8"; enabled: !window.learningDialogOpen && dashboard.currentPage === "studio" && !newModelField.activeFocus; onActivated: dashboard.command(56) } | command |
| dashboard.qml | 324 | Timer { interval: 700; running: dashboard.stage === "splash"; onTriggered: dashboard.finishSplash() } | finishSplash |
| dashboard.qml | 364 | onClicked: dashboard.beginHandSetup() | beginHandSetup |
| dashboard.qml | 389 | onClicked: dashboard.cancelHandSetup() | cancelHandSetup |
| dashboard.qml | 424 | onClicked: seg.picked(modelData[0]) | QML 내부/다른 뷰모델 |
| dashboard.qml | 485 | ActionButton { text: "고급 설정"; onClicked: { dashboard.command(27); advancedSettings.open() } } | command |
| dashboard.qml | 486 | ActionButton { text: "학습 센터"; onClicked: trainingCenter.open() } | QML 내부/다른 뷰모델 |
| dashboard.qml | 492 | onClicked: dashboard.command(32) | command |
| dashboard.qml | 518 | onClicked: dashboard.setPage(modelData[0]) | setPage |
| dashboard.qml | 539 | onClicked: trainingCenter.open() | QML 내부/다른 뷰모델 |
| dashboard.qml | 553 | onClicked: { dashboard.command(27); settingsDialog.open() } | command |
| dashboard.qml | 636 | MouseArea { anchors.fill: parent; onClicked: previewBox.large = !previewBox.large } | QML 내부/다른 뷰모델 |
| dashboard.qml | 692 | ActionButton { text: "취소"; onClicked: dashboard.cancelPointerCalibration() } | cancelPointerCalibration |
| dashboard.qml | 714 | ActionButton { text: "학습 센터 열기"; onClicked: trainingCenter.open() } | QML 내부/다른 뷰모델 |
| dashboard.qml | 760 | ActionButton { visible: dashboard.recWaiting; text: "시작"; accent: true; onClicked: dashboard.recStart() } | recStart |
| dashboard.qml | 761 | ActionButton { visible: dashboard.recMode === "guided" && !dashboard.recWaiting; text: dashboard.recPaused ? "계속" : "일시정지"; onClicked: dashboard.toggleRecordingPause() } | toggleRecordingPause |
| dashboard.qml | 762 | ActionButton { visible: dashboard.recMode !== "off"; text: "녹화 끝"; onClicked: dashboard.setRecMode("off") } | setRecMode |
| dashboard.qml | 773 | onClicked: dashboard.beginTutorial() | beginTutorial |
| dashboard.qml | 823 | ActionButton { text: diagnostics.visible ? "상세 닫기" : "상세 정보"; onClicked: diagnostics.visible = !diagnostics.visible } | QML 내부/다른 뷰모델 |
| dashboard.qml | 1019 | onToggled: dashboard.setStudioDiscrepancyOnly(checked) | setStudioDiscrepancyOnly |
| dashboard.qml | 1030 | onClicked: dashboard.setStudioDiscrepancyOnly(!dashboard.studioDiscrepancyOnly) | setStudioDiscrepancyOnly |
| dashboard.qml | 1041 | onToggled: dashboard.setStudioRequireFullJoints(checked) | setStudioRequireFullJoints |
| dashboard.qml | 1052 | onClicked: dashboard.setStudioRequireFullJoints(!dashboard.studioRequireFullJoints) | setStudioRequireFullJoints |
| dashboard.qml | 1084 | onClicked: dashboard.triggerStudioCapture() | triggerStudioCapture |
| dashboard.qml | 1089 | onClicked: dashboard.undoStudioSample() | undoStudioSample |
| dashboard.qml | 1094 | onClicked: dashboard.clearStudioSamples() | clearStudioSamples |
| dashboard.qml | 1099 | onClicked: dashboard.saveStudioDataset() | saveStudioDataset |
| dashboard.qml | 1104 | onClicked: dashboard.trainStudioModel() | trainStudioModel |
| dashboard.qml | 1123 | onClicked: dashboard.openStudioSnapshotsFolder() | openStudioSnapshotsFolder |
| dashboard.qml | 1190 | onClicked: dashboard.setStudioCategory(modelData.id) | setStudioCategory |
| dashboard.qml | 1264 | onClicked: dashboard.setStudioGroundTruth(modelData.id) | setStudioGroundTruth |
| dashboard.qml | 1332 | onClicked: { | QML 내부/다른 뷰모델 |
| dashboard.qml | 1342 | onClicked: trainingCenter.open() | QML 내부/다른 뷰모델 |
| dashboard.qml | 1357 | onAccepted: { | QML 내부/다른 뷰모델 |
| dashboard.qml | 1367 | onClicked: { | QML 내부/다른 뷰모델 |
| dashboard.qml | 1376 | onClicked: newModelInputRow.visible = false | QML 내부/다른 뷰모델 |
| dashboard.qml | 1434 | onClicked: dashboard.activateStudioModel(modelData.id) | activateStudioModel |
| dashboard.qml | 1440 | onClicked: dashboard.deleteStudioModel(modelData.id) | deleteStudioModel |
| dashboard.qml | 1490 | MouseArea { anchors.fill: parent; onClicked: dashboard.selectProfile(modelData[0]) } | selectProfile |
| dashboard.qml | 1860 | onClicked: dashboard.closeTrackingTest() | closeTrackingTest |
| LearningCenter.qml | 44 | StudioButton { objectName: "openTrainingProgress"; text: "학습 진행"; primary: studio.controller.trainingRunning; onClicked: {studio.close();studio.progressRequested()} } | QML 내부/다른 뷰모델 |
| LearningCenter.qml | 45 | StudioButton { text: "새로고침"; enabled: studio.idle; onClicked: {studio.controller.refreshTraining();studio.library.refresh()} } | refreshTraining |
| LearningCenter.qml | 46 | StudioButton { text: "닫기"; onClicked: studio.close() } | QML 내부/다른 뷰모델 |
| LearningCenter.qml | 58 | maximumLength: 32; text: studio.profileDraft; onTextEdited: studio.profileDraft=text | QML 내부/다른 뷰모델 |
| LearningCenter.qml | 60 | onAccepted: studio.controller.saveTrainingProfile(text) | saveTrainingProfile |
| LearningCenter.qml | 62 | StudioButton { objectName: "saveTrainingAlias"; text: studio.controller.trainingProfile===studio.profileDraft && studio.profileDraft!=="" ? "저장됨" : "별명 저장"; enabled: studio.idle; onClicked: studio.controller.saveTrainingProfile(alias.text) } | saveTrainingProfile |
| LearningCenter.qml | 64 | StudioButton { text: "학습용"; primary: studio.controller.recordingSplit==="train"; enabled: studio.idle; onClicked: studio.controller.setRecordingSplit("train") } | setRecordingSplit |
| LearningCenter.qml | 65 | StudioButton { text: "별도 평가"; primary: studio.controller.recordingSplit==="eval"; enabled: studio.idle; onClicked: studio.controller.setRecordingSplit("eval") } | setRecordingSplit |
| LearningCenter.qml | 98 | onClicked: studio.pageIndex=index | QML 내부/다른 뷰모델 |
| LearningCenter.qml | 113 | StudioButton { required property string modelData; text: modelData; primary: studio.controller.trainingFilter===modelData; onClicked: studio.controller.setTrainingFilter(modelData) } | setTrainingFilter |
| LearningCenter.qml | 118 | StudioField { Layout.fillWidth: true; placeholderText: "동작 이름 검색"; Accessible.name: "동작 검색"; onTextEdited: studio.search=text } | QML 내부/다른 뷰모델 |
| LearningCenter.qml | 119 | StudioButton { text: "모두 선택"; onClicked: tasks.model.forEach(function(r){studio.controller.setTrainingSelected(r.key,true)}) } | setTrainingSelected |
| LearningCenter.qml | 120 | StudioButton { text: "해제"; onClicked: tasks.model.forEach(function(r){studio.controller.setTrainingSelected(r.key,false)}) } | setTrainingSelected |
| LearningCenter.qml | 122 | StudioButton { objectName: "selectRecordedTraining"; text: "녹화된 학습 지원 동작 선택"; enabled: studio.idle; onClicked: studio.controller.selectRecordedTraining() } | selectRecordedTraining |
| LearningCenter.qml | 139 | StudioCheck { objectName: "taskCheck_"+task.modelData.key; checked: task.modelData.selected; Accessible.name: task.modelData.name+" 선택"; onClicked: {studio.detailKey=task.modelData.key;studio.controller.setTrainingSelected(task.modelData.key,checked)} } | setTrainingSelected |
| LearningCenter.qml | 163 | StudioButton { objectName: "focusRecord_"+(studio.detail.key &#124;&#124; ""); text: "집중 녹화 설정"; enabled: studio.idle && !!studio.detail.key; onClicked: {if(studio.controller.focusRecord(studio.detail.key)){studio.close();studio.recordingRequested()}} } | focusRecord |
| LearningCenter.qml | 164 | StudioButton { objectName: "trainGesture_"+(studio.detail.key &#124;&#124; ""); text: "이 동작만 학습"; enabled: studio.idle && !!studio.detail.can_train && Number(studio.detail.reps &#124;&#124; 0)>0; onClicked: studio.controller.trainSelectedGesture(studio.detail.key) } | trainSelectedGesture |
| LearningCenter.qml | 170 | StudioButton { required property var modelData; text: modelData.name; primary: studio.detail.tcn_mode===modelData.mode; enabled: studio.idle && !studio.detail.import_pending; onClicked: studio.controller.setGestureTcnMode(studio.detail.key,modelData.mode,studio.detail.model_id) } | setGestureTcnMode |
| LearningCenter.qml | 227 | StudioButton { objectName: "studioInclude"; text: studio.library.selected.included ? "학습에서 제외" : "학습에 포함"; enabled: studio.idle && studio.library.selected.split==="train" && studio.library.selected.complete===true; onClicked: studio.library.include(!studio.library.selected.included) } | QML 내부/다른 뷰모델 |
| LearningCenter.qml | 228 | StudioButton { text: "보정본 만들기"; enabled: studio.idle && studio.library.selected.split==="train" && studio.library.selected.derived==="original"; onClicked: studio.library.transform("clean") } | QML 내부/다른 뷰모델 |
| LearningCenter.qml | 229 | StudioButton { text: "증강본 만들기"; enabled: studio.idle && studio.library.selected.split==="train" && studio.library.selected.derived==="original"; onClicked: studio.library.transform("augment") } | QML 내부/다른 뷰모델 |
| LearningCenter.qml | 230 | StudioButton { text: "휴지통으로"; enabled: studio.idle && studio.library.selectedKey!==""; onClicked: {studio.confirmAction="trash";confirmation.open()} } | QML 내부/다른 뷰모델 |
| LearningCenter.qml | 235 | StudioButton { required property var modelData; Layout.fillWidth: true; flat: true; text: Number(modelData.start_t).toFixed(2)+"초 · "+modelData.message; onClicked: studio.library.seekTime(modelData.start_t) } | QML 내부/다른 뷰모델 |
| LearningCenter.qml | 248 | StudioButton { objectName: "simulateRecording"; text: "현재 모델로 비교"; primary: true; enabled: studio.idle && studio.library.selectedKey!==""; onClicked: studio.library.simulate() } | QML 내부/다른 뷰모델 |
| LearningCenter.qml | 256 | delegate: StudioButton { required property var modelData; width: ListView.view.width; flat: true; text: Number(modelData.t).toFixed(2)+"초  ·  "+modelData.text; onClicked: studio.library.seekTime(modelData.t) } | QML 내부/다른 뷰모델 |
| LearningCenter.qml | 273 | StudioButton { objectName: "recordSelection"; text: "선택 동작 녹화"; primary: true; enabled: studio.idle && studio.controller.selectedTrainingKeys.length>0; onClicked: {if(studio.controller.recordTrainingSelection()){studio.close();studio.recordingRequested()}} } | recordTrainingSelection |
| LearningCenter.qml | 274 | StudioButton { objectName: "trainSelection"; text: "선택 동작만 학습"; enabled: studio.idle && studio.controller.selectionCanTrain; onClicked: studio.controller.trainTrainingSelection() } | trainTrainingSelection |
| LearningCenter.qml | 275 | StudioButton { text: "중단"; visible: studio.controller.trainingRunning &#124;&#124; studio.library.busy; onClicked: {studio.controller.stopTraining();studio.library.cancel()} } | stopTraining |
| LearningCenter.qml | 302 | StudioButton { text: "취소"; onClicked: confirmation.close() } | QML 내부/다른 뷰모델 |
| LearningCenter.qml | 303 | StudioButton { text: "진행"; primary: true; enabled: studio.idle; onClicked: confirmation.accept() } | QML 내부/다른 뷰모델 |
| QuickSpeedControls.qml | 31 | onMoved: saveDelay.restart() | QML 내부/다른 뷰모델 |
| QuickSpeedControls.qml | 34 | onTriggered: { | QML 내부/다른 뷰모델 |
| QuickSpeedControls.qml | 53 | onClicked: settings.controller.setShowDesktopEnabled(checked) | setShowDesktopEnabled |
| RecordingPlanDialog.qml | 43 | onActivated: dialog.editor.setField(choice.field, currentText) | QML 내부/다른 뷰모델 |
| RecordingPlanDialog.qml | 71 | StudioButton { text: "닫기"; onClicked: dialog.close() } | QML 내부/다른 뷰모델 |
| RecordingPlanDialog.qml | 76 | StudioButton { text: "기본 3자세"; onClicked: dialog.editor.usePreset("basic") } | QML 내부/다른 뷰모델 |
| RecordingPlanDialog.qml | 77 | StudioButton { text: "다양한 조건 9자세"; onClicked: dialog.editor.usePreset("diverse") } | QML 내부/다른 뷰모델 |
| RecordingPlanDialog.qml | 78 | StudioButton { text: "화면 위치 9곳"; onClicked: dialog.editor.usePreset("positions") } | QML 내부/다른 뷰모델 |
| RecordingPlanDialog.qml | 79 | StudioButton { objectName: "quickClickPreset"; text: "짧은 클릭 · 각도×속도"; visible: dialog.editor.clickGesture; onClicked: dialog.editor.usePreset("quick_click") } | QML 내부/다른 뷰모델 |
| RecordingPlanDialog.qml | 114 | onClicked: { dialog.editor.select(index); dialog.editor.setField("enabled", checked) } | QML 내부/다른 뷰모델 |
| RecordingPlanDialog.qml | 120 | onClicked: dialog.editor.select(index) | QML 내부/다른 뷰모델 |
| RecordingPlanDialog.qml | 132 | StudioButton { Layout.fillWidth: true; implicitWidth: 0; text: "추가"; onClicked: dialog.editor.add(false) } | QML 내부/다른 뷰모델 |
| RecordingPlanDialog.qml | 133 | StudioButton { Layout.fillWidth: true; implicitWidth: 0; text: "복제"; onClicked: dialog.editor.add(true) } | QML 내부/다른 뷰모델 |
| RecordingPlanDialog.qml | 134 | StudioButton { Layout.fillWidth: true; implicitWidth: 0; text: "빼기"; onClicked: dialog.editor.remove() } | QML 내부/다른 뷰모델 |
| RecordingPlanDialog.qml | 156 | onEditingFinished: dialog.editor.setField("name", text) | QML 내부/다른 뷰모델 |
| RecordingPlanDialog.qml | 173 | onEditingFinished: dialog.editor.setField("notes", text) | QML 내부/다른 뷰모델 |
| RecordingPlanDialog.qml | 223 | StudioButton { text: "설정 저장"; onClicked: dialog.editor.save() } | QML 내부/다른 뷰모델 |
| RecordingPlanDialog.qml | 227 | onClicked: { if (dialog.editor.start()) dialog.close() } | QML 내부/다른 뷰모델 |
| studio/dashboard.qml | 85 | onMoved: dashboard.setPointerSensitivity(Math.round(value)) | setPointerSensitivity |
| studio/dashboard.qml | 93 | ActionButton { text: "기본값 복원"; onClicked: dashboard.setPointerSensitivity(100) } | setPointerSensitivity |
| studio/dashboard.qml | 96 | onClicked: { | QML 내부/다른 뷰모델 |
| studio/dashboard.qml | 104 | Shortcut { sequence: "Return"; enabled: !window.learningDialogOpen && !dashboard.trackingActive && (dashboard.calibratingPointer); autoRepeat: false; onActivated: dashboard.capturePointerSample() } | capturePointerSample |
| studio/dashboard.qml | 105 | Shortcut { sequence: "Enter"; enabled: !window.learningDialogOpen && !dashboard.trackingActive && (dashboard.calibratingPointer); autoRepeat: false; onActivated: dashboard.capturePointerSample() } | capturePointerSample |
| studio/dashboard.qml | 106 | Shortcut { sequence: "Esc"; enabled: !window.learningDialogOpen && !dashboard.trackingActive; onActivated: dashboard.command(27) } | command |
| studio/dashboard.qml | 107 | Shortcut { sequence: "Space"; enabled: !window.learningDialogOpen && !dashboard.trackingActive && (!pointerSettings.visible); onActivated: dashboard.command(32) } | command |
| studio/dashboard.qml | 108 | Shortcut { sequence: "Q"; enabled: !window.learningDialogOpen && !dashboard.trackingActive; onActivated: dashboard.command(81) } | command |
| studio/dashboard.qml | 109 | Shortcut { sequence: "O"; enabled: !window.learningDialogOpen && !dashboard.trackingActive; onActivated: dashboard.command(111) } | command |
| studio/dashboard.qml | 110 | Shortcut { sequence: "M"; enabled: !window.learningDialogOpen && !dashboard.trackingActive; onActivated: dashboard.command(109) } | command |
| studio/dashboard.qml | 111 | Shortcut { sequence: "N"; enabled: !window.learningDialogOpen && !dashboard.trackingActive; onActivated: dashboard.command(110) } | command |
| studio/dashboard.qml | 112 | Shortcut { sequence: "F"; enabled: !window.learningDialogOpen && !dashboard.trackingActive; onActivated: dashboard.markFailure() } | markFailure |
| studio/dashboard.qml | 113 | Shortcut { sequence: "C"; enabled: !window.learningDialogOpen && !dashboard.trackingActive; onActivated: dashboard.command(99) } | command |
| studio/dashboard.qml | 114 | Shortcut { sequence: "P"; enabled: !window.learningDialogOpen && !dashboard.trackingActive; onActivated: dashboard.command(112) } | command |
| studio/dashboard.qml | 116 | Timer { interval: 700; running: dashboard.stage === "splash"; onTriggered: dashboard.finishSplash() } | finishSplash |
| studio/dashboard.qml | 156 | onClicked: dashboard.beginHandSetup() | beginHandSetup |
| studio/dashboard.qml | 181 | onClicked: dashboard.cancelHandSetup() | cancelHandSetup |
| studio/dashboard.qml | 222 | onClicked: seg.picked(modelData[0]) | QML 내부/다른 뷰모델 |
| studio/dashboard.qml | 276 | ActionButton { objectName: "openLearningCenter"; text: window.width < 1000 ? "학습" : "학습 센터"; onClicked: {dataCenter.pageIndex=0;dataCenter.open()} } | QML 내부/다른 뷰모델 |
| studio/dashboard.qml | 277 | ActionButton { objectName: "openDataStudio"; text: window.width < 1000 ? "녹화" : "녹화·시뮬레이터"; onClicked: {dataCenter.pageIndex=1;dataCenter.open()} } | QML 내부/다른 뷰모델 |
| studio/dashboard.qml | 280 | onClicked: dashboard.beginHandSetup() | beginHandSetup |
| studio/dashboard.qml | 285 | onClicked: { | QML 내부/다른 뷰모델 |
| studio/dashboard.qml | 294 | ActionButton { visible: window.width >= 1000; text: "고급 설정"; onClicked: { dashboard.command(27); advancedSettings.open() } } | command |
| studio/dashboard.qml | 295 | ActionButton { objectName: "mainControlToggle"; text: dashboard.controlLabel; accent: true; onClicked: dashboard.command(32) } | command |
| studio/dashboard.qml | 314 | ActionButton { text: "동작 다시 배우기"; Layout.fillWidth: true; onClicked: dashboard.beginTutorial() } | beginTutorial |
| studio/dashboard.qml | 335 | onClicked: dashboard.toggleMotion() | toggleMotion |
| studio/dashboard.qml | 341 | onClicked: dashboard.cycleProfile() | cycleProfile |
| studio/dashboard.qml | 351 | onClicked: dashboard.beginPalmCalibration() | beginPalmCalibration |
| studio/dashboard.qml | 356 | onClicked: dashboard.resetPalmCalibration() | resetPalmCalibration |
| studio/dashboard.qml | 410 | onClicked: dashboard.beginPointerCalibration() | beginPointerCalibration |
| studio/dashboard.qml | 415 | onClicked: dashboard.resetPointerCalibration() | resetPointerCalibration |
| studio/dashboard.qml | 474 | onClicked: dashboard.beginTrackingTest() | beginTrackingTest |
| studio/dashboard.qml | 512 | onClicked: dashboard.markFailure() | markFailure |
| studio/dashboard.qml | 612 | ActionButton { text: "취소"; onClicked: dashboard.cancelPointerCalibration() } | cancelPointerCalibration |
| studio/dashboard.qml | 658 | ActionButton { visible: dashboard.recWaiting; text: "시작"; onClicked: dashboard.recStart() } | recStart |
| studio/dashboard.qml | 659 | ActionButton { visible: dashboard.recMode === "guided" && !dashboard.recWaiting; text: dashboard.recPaused ? "계속" : "일시정지"; onClicked: dashboard.toggleRecordingPause() } | toggleRecordingPause |
| studio/dashboard.qml | 660 | ActionButton { visible: dashboard.recMode !== "off"; text: "녹화 끝"; onClicked: dashboard.setRecMode("off") } | setRecMode |
| studio/dashboard.qml | 670 | ActionButton { visible: dashboard.palmCalibrating; text: "취소"; onClicked: dashboard.cancelPalmCalibration() } | cancelPalmCalibration |
| studio/dashboard.qml | 680 | ActionButton { text: diagnostics.visible ? "상세 닫기" : "상세 정보"; onClicked: diagnostics.visible = !diagnostics.visible } | QML 내부/다른 뷰모델 |
| studio/dashboard.qml | 748 | onClicked: { | QML 내부/다른 뷰모델 |
| studio/dashboard.qml | 753 | ActionButton { text: "새로고침"; enabled: dashboard.trainingBusy === ""; onClicked: dashboard.refreshTraining() } | refreshTraining |
| studio/dashboard.qml | 754 | ActionButton { text: "닫기"; onClicked: trainingCenter.close() } | QML 내부/다른 뷰모델 |
| studio/dashboard.qml | 798 | onClicked: dashboard.setTrainingFilter(modelData) | setTrainingFilter |
| studio/dashboard.qml | 809 | onClicked: dashboard.selectAllTraining(true) | selectAllTraining |
| studio/dashboard.qml | 814 | onClicked: dashboard.selectAllTraining(false) | selectAllTraining |
| studio/dashboard.qml | 860 | onClicked: dashboard.selectTrainingTask(row.modelData.key, checked) | selectTrainingTask |
| studio/dashboard.qml | 918 | onActivated: dashboard.setTaskTcnMode(row.modelData.key, currentIndex) | setTaskTcnMode |
| studio/dashboard.qml | 941 | onClicked: dashboard.trainSelectedGesture(row.modelData.key) | trainSelectedGesture |
| studio/dashboard.qml | 948 | onClicked: { | QML 내부/다른 뷰모델 |
| studio/dashboard.qml | 1000 | onClicked: dashboard.trainingRunning ? dashboard.stopTraining() : dashboard.startTraining() | stopTraining, startTraining |
| studio/dashboard.qml | 1005 | onClicked: dashboard.exportModel() | exportModel |
| studio/dashboard.qml | 1007 | ActionButton { text: "폴더 열기"; onClicked: dashboard.openExports() } | openExports |
| studio/dashboard.qml | 1216 | onClicked: dashboard.closeTrackingTest() | closeTrackingTest |
| studio/RecordingPlanDialog.qml | 39 | onActivated: dialog.editor.setField(choice.field, currentText) | QML 내부/다른 뷰모델 |
| studio/RecordingPlanDialog.qml | 68 | Button { text: "닫기"; onClicked: dialog.close() } | QML 내부/다른 뷰모델 |
| studio/RecordingPlanDialog.qml | 73 | Button { text: "기본 3자세"; onClicked: dialog.editor.usePreset("basic") } | QML 내부/다른 뷰모델 |
| studio/RecordingPlanDialog.qml | 74 | Button { text: "다양한 조건 9자세"; onClicked: dialog.editor.usePreset("diverse") } | QML 내부/다른 뷰모델 |
| studio/RecordingPlanDialog.qml | 75 | Button { text: "화면 위치 9곳"; onClicked: dialog.editor.usePreset("positions") } | QML 내부/다른 뷰모델 |
| studio/RecordingPlanDialog.qml | 76 | Button { text: "짧은 클릭 · 각도×속도"; visible: dialog.editor.clickGesture; onClicked: dialog.editor.usePreset("quick_click") } | QML 내부/다른 뷰모델 |
| studio/RecordingPlanDialog.qml | 111 | onClicked: { dialog.editor.select(index); dialog.editor.setField("enabled", checked) } | QML 내부/다른 뷰모델 |
| studio/RecordingPlanDialog.qml | 116 | onClicked: dialog.editor.select(index) | QML 내부/다른 뷰모델 |
| studio/RecordingPlanDialog.qml | 128 | Button { Layout.fillWidth: true; implicitWidth: 0; text: "추가"; onClicked: dialog.editor.add(false) } | QML 내부/다른 뷰모델 |
| studio/RecordingPlanDialog.qml | 129 | Button { Layout.fillWidth: true; implicitWidth: 0; text: "복제"; onClicked: dialog.editor.add(true) } | QML 내부/다른 뷰모델 |
| studio/RecordingPlanDialog.qml | 130 | Button { Layout.fillWidth: true; implicitWidth: 0; text: "빼기"; onClicked: dialog.editor.remove() } | QML 내부/다른 뷰모델 |
| studio/RecordingPlanDialog.qml | 150 | onEditingFinished: dialog.editor.setField("name", text) | QML 내부/다른 뷰모델 |
| studio/RecordingPlanDialog.qml | 167 | onEditingFinished: dialog.editor.setField("notes", text) | QML 내부/다른 뷰모델 |
| studio/RecordingPlanDialog.qml | 214 | Button { text: "설정 저장"; onClicked: dialog.editor.save() } | QML 내부/다른 뷰모델 |
| studio/RecordingPlanDialog.qml | 218 | onClicked: { if (dialog.editor.start()) dialog.close() } | QML 내부/다른 뷰모델 |
| studio/SettingsDialog.qml | 83 | onMoved: settings.changeValue(modelData.key, Number(value.toFixed(6))) | QML 내부/다른 뷰모델 |
| studio/SettingsDialog.qml | 93 | onTextEdited: settings.changeValue(modelData.key, text.trim() ? Number(text) : NaN) | QML 내부/다른 뷰모델 |
| studio/SettingsDialog.qml | 110 | onMoved: settings.controller.setPointerSensitivity(Math.round(value)) | setPointerSensitivity |
| studio/SettingsDialog.qml | 119 | onClicked: settings.controller.setFingerOverlay(checked) | setFingerOverlay |
| studio/SettingsDialog.qml | 124 | onClicked: settings.controller.setOverlayVisible(checked) | setOverlayVisible |
| studio/SettingsDialog.qml | 129 | onClicked: settings.dataRequested() | QML 내부/다른 뷰모델 |
| studio/SettingsDialog.qml | 148 | onClicked: { | QML 내부/다른 뷰모델 |
| studio/SettingsDialog.qml | 159 | onClicked: settings.controller.applySensitivity(settings.edits) | applySensitivity |
| studio/SettingsDialog.qml | 161 | Button { text: "고급 설정"; onClicked: { settings.close(); settings.advancedRequested() } } | QML 내부/다른 뷰모델 |
| studio/SettingsDialog.qml | 162 | Button { text: "닫기"; onClicked: settings.close() } | QML 내부/다른 뷰모델 |
| studio/TrainingProgressPage.qml | 77 | onClicked: page.backRequested() | QML 내부/다른 뷰모델 |
| studio/TrainingProgressPage.qml | 79 | Action { text: "닫기"; onClicked: page.close() } | QML 내부/다른 뷰모델 |
| studio/TrainingProgressPage.qml | 248 | onClicked: controller.stopTraining() | stopTraining |
| TrainingProgressPage.qml | 41 | text: "학습센터로"; onClicked: { page.close(); page.backRequested() } | QML 내부/다른 뷰모델 |
| TrainingProgressPage.qml | 43 | StudioButton { text: "닫기"; onClicked: page.close() } | QML 내부/다른 뷰모델 |
| TrainingProgressPage.qml | 104 | StudioButton { text: "학습 중단"; enabled: page.state.cancellable === true; onClicked: page.controller.stopTraining() } | stopTraining |
| TrainingProgressPage.qml | 108 | StudioButton { text: page.expandedEvents ? "접기" : "전체 기록 보기"; onClicked: page.expandedEvents = !page.expandedEvents } | QML 내부/다른 뷰모델 |
| TutorialPage.qml | 109 | Button { text: "이전"; visible: page.controller.tutorialIndex > 0 && !page.controller.tutorialFinished; onClicked: page.controller.tutorialBack() } | tutorialBack |
| TutorialPage.qml | 110 | Button { text: "다시 시도"; visible: page.controller.tutorialFailures > 0 && !page.controller.tutorialFinished; onClicked: page.controller.tutorialRetry() } | tutorialRetry |
| TutorialPage.qml | 112 | Button { text: "나중에 배우기"; visible: !page.controller.tutorialFinished; onClicked: page.controller.finishTutorial() } | finishTutorial |
| TutorialPage.qml | 117 | onClicked: page.controller.tutorialFinished ? page.controller.finishTutorial() : page.controller.tutorialNext() | finishTutorial, tutorialNext |
