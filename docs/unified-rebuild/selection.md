# 2026-11-05 새 앱 구성 기준

사용자 요청: 현재 앱을 보완하는 대신 GitHub와 Jira의 팀 자료를 모두 확인하고, 잘된 부분을 골라 새 앱으로 구성한다.
새 앱은 `apps/manux/`에서 실행하며 기존 웹앱, `hand-mouse/`, 각 팀원 브랜치는 보존한다.
시작 기준은 preview `36c3a01`이다. 원격 12개 브랜치와 Jira SCRUM 113개 작업을 확인했다.
정확한 Git SHA는 [sources.json](sources.json), Jira 확인 범위는 [jira-index.json](jira-index.json)에 기록한다.
Jira 첨부는 기존 착수보고 PPT 1개였으며, 구현 자료는 설명·코멘트의 GitHub 링크에 있었다.

## 선별

| 출처 | 새 앱에 활용할 부분 | 그대로 가져오지 않는 부분 |
|---|---|---|
| Dan / hand-mouse-gesture-overhaul `7448e4a` | 핀치 손가락 독점·이웃 손가락 구분, 재누름 히스테리시스, 방향 보정 수학, 선택 가능한 궤적 보정, 손 기준 3D 특징, 표본·평가 분리 | 집 PC의 폰 카메라에 맞춘 전체 임계값, 주먹 최대 35배 가속, 자동 창 닫기·바탕화면 동작 |
| Park / park `be4016f` | 손바닥 직교 좌표와 깊이 감쇠, 클릭 전 보이던 위치 고정, 클릭을 가리지 않는 HUD, 작업 영역 표시 | 기존 프로젝트 115개 파일 변경·삭제, 여러 스크롤 후보의 동시 활성화 |
| 팀 preview / production | 모니터 레이아웃, 명시적 PC 제어 켜기·끄기, 개인 프로필, 설정 보존, 입력 watchdog·제한된 IPC | 새 앱 검증 전 production·자동 업데이트 교체 |
| 이전 독립 Tracking/Fist/Touch 실험 | 최신 프레임 캡처, 가림 복구, 손 연결 유지, 연속 커서 출력, 손 재진입 시 재연결, 기능별 실제 이벤트 연습 | 오래된 UI 흐름, 한 번에 여러 동작 등록, 판정 횟수를 성공률로 표시 |
| 동작 개별 등록 / SCRUM-23·24·27·30 | 하나씩 준비→수집→별도 시험→저장, 실패·취소 시 기존 프로필 보존, 입력 억제 | 불확실한 등록 결과를 기본 명령으로 우회 |
| gaze-experiment / feasibility-research | 추적·의도·OS 결과를 따로 측정하는 검증 기준 | 검증되지 않은 시선 좌표를 마우스 입력에 연결 |
| product-direction-in-bed | 책상·거리·조명 조건을 구분해 기록 | 자료만으로 저조도·원거리 성능을 보장 |

## TCN의 적용 범위

현재 Dan의 TCN은 `none / thumb_index / thumb_ring`만 학습하며 우클릭·검지 핀치 스크롤을 확인하는 보조 장치다.
검지 누르기, 편 손 드래그, 확대를 학습한 모델이 아니다. 따라서 이 모델을 새 동작 전체에 적용하거나
실사용 정확도 향상을 보장하지 않는다. 손 기준 특징과 학습용 기록 계약을 활용하고, 모델은 별도 시험 대상으로 둔다.
공개 브랜치에 있더라도 개인 녹화·실패 기록 원본은 새 앱 소스나 시험 fixture에 복제하지 않는다.

`solution.md`의 원거리·적외선 100% 인식 주장은 해당 조건의 검증 자료가 아니다.
새 앱의 인수 기준이나 홍보 문구로 사용하지 않는다.

## 새 앱의 경계

카메라/손 선택 → 관절 특징 → 동작 상태기계 → 단일 입력 라우터 → 연습 또는 Windows.
UI는 판정을 추가로 만들지 않는다. 선택한 손과 동작 프리셋, 손 이동 영역, 설정은 한 프로필에 저장한다.
전환·설정·등록·손 소실 시 잡은 버튼을 해제하고, 다시 나타난 손은 현재 커서 위치에 연결한다.
배율 확정과 잡기 해제는 별도 종료 상태로 처리한다.

관련 Jira: [SCRUM-105](https://se4nchoi.atlassian.net/browse/SCRUM-105),
[SCRUM-78](https://se4nchoi.atlassian.net/browse/SCRUM-78),
[SCRUM-102](https://se4nchoi.atlassian.net/browse/SCRUM-102),
[SCRUM-89](https://se4nchoi.atlassian.net/browse/SCRUM-89),
[SCRUM-27](https://se4nchoi.atlassian.net/browse/SCRUM-27).

이 문서는 구현 시작 기준이다. 실제 채택·검증 결과와 미완료 항목은 후속 결과 기록에 남긴다.
