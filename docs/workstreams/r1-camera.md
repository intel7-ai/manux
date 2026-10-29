# R1 비전·카메라·성능 작업 지침

담당 **김동혁**, 검토 **라즈**. Jira [SCRUM-6](https://se4nchoi.atlassian.net/browse/SCRUM-6), 공동 검증 [SCRUM-12](https://se4nchoi.atlassian.net/browse/SCRUM-12). 권장 브랜치 `codex/scrum-6-camera`. 이 문서는 구현 전 작업 계획이다. 인수인계 준비에서는 앱 코드를 바꾸지 않았으며, 아래 내용은 담당자가 자기 브랜치에서 수행할 과제다.

## 목표

실제 등록 실패가 손 검출·입력 품질·기울기·좌우 손 판정·추론 간격 중 어느 단계와 관련 있는지 증거를 만든다. 필요한 카메라와 추론 개선을 수행하되 다른 팀원이 같은 입력을 재현할 수 있게 한다.

## 파일 경계

- 주 담당: `src/camera.js`, `src/vision-client.js`, `src/vision-worker.js`, `src/orientation.js`.
- 시험: `tests/camera.test.js`, `tests/vision-client.test.js`, `tests/orientation.test.js`, `scripts/benchmark-vision.mjs`.
- R3 협의: `getHandQuality`, `extractFeatures`의 좌표·종횡비·손 정보. 이 함수는 `src/personalization.js`에 있으므로 직접 병렬 수정하지 않는다.
- R4 협의: 기울기 보류 이유와 화면의 목표 방향. R5 협의: `src/app.js`의 결과 연결·종료·로그.
- 제안 산출물: `docs/evidence/r1-camera.md`, `docs/evidence/r1-measurements.csv`. 현재 존재하는 결과로 오해하지 않는다.

## 순서

1. 공통 기준 커밋과 카메라 모델·브라우저·조명·실제 해상도를 기록한다. 480p/720p/1080p의 요청값과 실제값을 분리한다.
2. R4와 실제 등록 실패를 재현한다. 손 검출 없음, 손 크기/잘림, 기울기 유효성, 목표 각도, 손 ID 변동, 관찰 간격을 같은 세션 로그로 남긴다. 어느 원인인지 추정만으로 확정하지 않는다.
3. 기준 자세에서 손을 평행 이동·평면 회전·실제 기울임으로 나눠 관찰한다. 단안 관절 기울기는 물리 각도 측정값으로 발표하지 않는다.
4. 카메라 종료·재시작·분리·늦은 응답을 시험한다. 세션이 바뀐 결과, 닫힌 프레임, 중복 준비 응답이 소비되지 않아야 한다.
5. 확인된 원인 하나씩 수정하고 같은 조건으로 전후 비교한다. 각도 문턱을 낮추는 경우 등록 완료뿐 아니라 잘못된 방향 수락도 함께 측정한다.
6. R3·R4·R5가 새 입력 계약을 소비하는지 확인하고 근거·한계를 PR에 남긴다.

## 계약

[공통 계약](../contracts.md)의 관측 사건을 따른다. 한 프레임에는 세션/순서, 메인 스레드 기준 시각, 실제 영상 크기, 관절, 좌우 손 정보, 품질·기울기 유효성과 보류 이유가 함께 있어야 한다. 한 번에 처리하는 프레임을 제한하고 오래된 입력을 쌓지 않는다. 카메라를 끄면 제어 해제는 R5가 맡지만 R1은 늦은 결과를 차단한다.

기울기 계산 변경은 R4 안내와 R3 수집 기준에 영향을 준다. 계약 PR에서 축 방향·미러링·허용 구간·실패 코드 변경을 명시한다.

## 검증과 인수

```powershell
node --test tests/camera.test.js tests/vision-client.test.js tests/orientation.test.js
node scripts/benchmark-vision.mjs
```

벤치의 실제 CLI·입력 조건은 스크립트를 읽고 사용한다. 벤치 사진 결과를 실물 거리 성능으로 쓰지 않는다. 통합 전 `npm test`와 관련 브라우저 시나리오를 수행한다.

- 실제 실패 세션에서 입력 문제와 모델/저장 문제를 구분할 수 있다.
- 해상도별 실제 설정, 추론 시간·결과 간격 중앙값/p95, 조건·표본 수가 있다.
- 종료·재연결·손 소실 후 과거 결과가 명령이나 등록 진행을 만들지 않는다.
- 기울기 변경에는 올바른/잘못된 방향·손 바뀜·가림 사례가 포함된다.
- 실측하지 않은 거리나 정확도를 지원 수치로 쓰지 않는다.

## 에이전트 시작 프롬프트

```text
MANUX의 R1을 담당한다. AGENTS.md, docs/master-plan.md, docs/contracts.md,
docs/workstreams/r1-camera.md, Jira SCRUM-6/12의 확인된 내용을 먼저 읽어라.
내 브랜치는 codex/scrum-6-camera다. 기준 커밋과 변경 상태를 확인하고
카메라·추론·기울기 파일 범위에서 작업한다. 등록 실패의 원인을 가정하지 말고
기존 시험과 실물 재현 절차를 먼저 정리하라. 실제 손 시험을 수행할 수 없으면
그 부분을 미검증으로 남기고 자동 시험·진단 도구는 완성하라.
app.js, enrollment.js, personalization.js는 담당자 계약 없이 함께 수정하지 마라.
수정한 문제, 근거, 시험 명령, 실패 조건, 실물 확인 필요 항목을 PR에 남겨라.
```

취업 증거: 실시간 비동기 처리, 성능 측정, 센서 입력 품질 진단. PR과 재현 로그를 [공동 평가](../master-plan.md#7-공통-인수-기준)에 연결한다.
