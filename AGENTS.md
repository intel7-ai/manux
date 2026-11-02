# MANUX 팀 에이전트 작업 지침

이 저장소는 로컬 웹캠으로 영상과 Windows 마우스를 제어하는 MANUX의 공동 작업 공간이다. 먼저 [README](README.md)와 [하네스](docs/harness.md)를 읽고, 작업에 필요한 [모듈 계약](docs/contracts.md)과 본인 [작업 문서](docs/workstreams/)만 추가로 읽는다. [마스터 플랜](docs/master-plan.md)은 과거 계획을 포함하므로 현재 구현과 구분한다. 사람의 현재 요청이 이 문서보다 우선한다.

## 현재 상태

- 2026-11-02 손 전체 이동 기반 마우스 모드와 Electron/Windows 입력 경로를 추가했다. 현재 계약·실행법은 `docs/air-mouse.md`와 `desktop/README.md`를 확인한다. 실제 웹캠 사용성과 실제 OS 클릭 검증은 별도다.

- 기본 네 명령, 선택적 ±10초 반복, 카메라·Worker, 사용자·환경 프로필, 여러 각도 등록 코드가 있다.
- 2026-10-29 사용자는 여러 동작을 한꺼번에 등록하는 부담과 실제 등록 실패를 보고했다. 원인은 아직 확인하지 않았다. 자동 시험 통과를 실사용 문제 해결이나 인식률 보장으로 표현하지 않는다.
- 동작 하나씩 등록, 임의의 `rock` 손 모양 추가, 자유로운 액션 바인딩은 후속 개발 사항이다. 문서의 제안 API를 현재 구현으로 설명하지 않는다.
- 현재 개인화는 관절 특징 예시 기반 분류다. 사전학습 MediaPipe 가중치를 파인튜닝하지 않는다.

## 브랜치와 담당 경계

작업을 시작할 때 `git status --short`, `git branch --show-current`, `git remote -v`로 실제 상태를 확인한다. 다른 사람의 변경을 되돌리거나 깨끗하게 만들기 위해 삭제하지 않는다. 담당 브랜치는 아래 권장 이름을 사용하되 사람의 지시와 실제 원격 기본 브랜치를 따른다.

통합 흐름은 [브랜치 흐름](docs/branch-flow.md)을 따른다. 작업 브랜치는 `origin/preview`에서 만들고 PR 대상은 `preview`다. 리뷰어 승인은 필요 없다. `production`에는 preview에서 시험한 뒤 `preview` 브랜치 PR로만 병합한다.

| 역할 | 담당 | 권장 브랜치 | 주 담당 파일 |
| --- | --- | --- | --- |
| R1 / SCRUM-6 | 김동혁 | `codex/scrum-6-camera` | `src/camera.js`, `src/vision-client.js`, `src/vision-worker.js`, `src/orientation.js` |
| R2 / SCRUM-7 | 박현규 | `codex/scrum-7-control` | `src/gesture-controller.js`, `src/media-controller.js` |
| R3 / SCRUM-8 | 라즈 | `codex/scrum-8-personalization` | `src/personalization.js`, 특징·데이터 규격 |
| R4 / SCRUM-9 | Paul Dan | `codex/scrum-9-onboarding` | `src/enrollment.js`, `src/enrollment-view.js`, `public/index.html`, `public/styles.css` |
| R5 / SCRUM-10 | Sean Choi | `codex/scrum-10-integration` | `src/profile-store.js`, `src/app.js`, `server.js`, 실행·통합 도구 |

각 담당은 대응 테스트도 관리한다. R6/SCRUM-11은 실제 참여 5명의 공동 평가 영역이며 SCRUM-12~16이 각 담당의 검증 작업이다. 문채원은 명단 등록 상태이며 작업을 배정하지 않는다.

`src/app.js`는 R5가 통합한다. 다른 역할에서 연결 변경이 필요하면 계약과 최소 연결 diff를 제안하고 R5와 조정한다. 한 PR에서 담당 영역 밖의 대규모 리팩터링을 하지 않는다. 계약 변경을 먼저 문서화하고 소비 모듈 담당자가 확인하도록 PR에 표시한다.

## 작업 순서

1. 현재 Git 상태를 확인하고 요청을 `harness/task.schema.json` 형식으로 정리한다. 비전공자에게 JSON 작성을 요구하지 않는다. 목표·완료 기준·허용 파일·기준 커밋을 기록하고, Jira가 없다는 이유만으로 진행을 막지 않는다.
2. 실패를 재현한다. 환경, 입력 순서, 예상/실제 결과를 기록하고 원인 가설을 사실과 분리한다.
3. 작은 변경을 구현한다. 독립 작업은 최대 5개 worktree에서 병렬 진행하며 같은 파일의 통합은 조정자가 순서대로 처리한다. worker는 다른 worker를 만들거나 공유 checkout을 수정하지 않는다.
4. `npm run verify`와 변경에 필요한 브라우저/Windows 검사를 실행한다. 실패를 숨기기 위해 테스트를 삭제하거나 기대값만 맞추지 않는다. 수정 뒤에는 관련 검증을 다시 실행한다.
5. `harness/result.schema.json` 형식으로 실제 결과·완료 기준별 증거·한계를 기록한다. `npm run task:report -- TASK.json RESULT.json`으로 검증된 보고서를 만들고 독립 리뷰 후 공유 checkout에 통합한다. 통합 뒤 다시 검증한다.

작업 기록은 `.local/harness/<requestId>/task.json`, `result.json`에 둔다. 폴더와 ID는 일치시킨다. worker 결과는 조정자가 공유 checkout의 같은 위치로 검토하여 옮긴다. Git에 올릴 PR에는 민감정보를 제거한 보고서와 검사 증거만 첨부한다.

## 모든 사용자에게 같은 응답

- 최종 보고서는 renderer가 만든 `상태 → 요약 → 변경 → 검증 → 다음` 순서를 그대로 사용한다. `complete`는 해당 요청의 검증 완료이며 공유 checkout 통합·GitHub 게시와 별개다. 통합/게시 여부는 사실대로 표시한다.
- 완료 기준이 충족되지 않았거나 검사를 실행하지 못했으면 `needs_review` 또는 `blocked`로 기록한다. 결과 계약의 검사는 형식과 증거 유무를 확인하며 내용의 진실성을 대신 보증하지 않는다.
- 질문·인사·접수·진행도 같은 다섯 항목을 쓴다. 예: `상태: 정보`, `요약: ...`, `변경: 없음`, `검증: 미실행(안내 답변)`, `다음: ...`. 간단한 안내에는 작업 JSON을 만들지 않는다.
- Discord의 `reply`와 `edit_message`는 `.claude/settings.json`의 전송 전 검사 훅을 거친다. 종료 상태는 기록과 renderer 출력이 일치해야 전송된다. 막히면 원인을 수정하고 재시도하며 HTTP·다른 도구로 우회하지 않는다. Discord 전송은 원래 사용자의 요청에 대한 답변만 한다.
- 기술 용어는 필요한 만큼만 설명하고 실행할 다음 행동을 적는다. 미실행·실패·가설을 완료·성공·사실로 바꾸지 않는다.

## 기술 경계

- 오래된 프레임을 쌓지 않고 동시에 한 프레임을 처리하는 계약, 세션 종료 후 결과 무시, 자원 정리를 보존한다.
- 등록 중 영상 명령을 실행하지 않는다. 취소·실패·검증 전 저장은 기존 유효 등록을 손상시키지 않아야 한다.
- 개인 모델이 거절한 입력을 공통 모델 명령으로 우회하지 않는다.
- 향후 부분 등록은 선택한 동작·사용자·환경만 교체해야 한다. 누락된 클래스, 중립, 미등록 입력, 바인딩 없는 동작의 실행 정책을 계약과 시험으로 명시한다.
- 동작 ID와 액션 ID를 분리한다. `rock`이라는 이름이 분류 결과나 OS 명령 그 자체라고 가정하지 않는다.
- 사용자 이름·개인 프로필·원시 웹캠 영상·계정 정보·키를 시험 fixture나 저장소에 넣지 않는다.

## 검증과 주장

`npm ci --ignore-scripts` 후 `npm run verify`가 로컬·CI 공통 검사다. 모델·웹캠 없이 실행되는 단위 시험과 합성 브라우저 시험, 실제 사용자 시험을 구분한다. 브라우저 검사 준비와 CI 실패 해결은 [CONTRIBUTING](CONTRIBUTING.md)을 따른다.

같은 손 자세의 인접 프레임은 독립적인 사용자 시도가 아니다. 등록과 평가 수행을 분리하고 성공률과 함께 오실행·지연·등록 실패를 기록한다. 테스트 수나 프레임 점수를 제품 정확도로 설명하지 않는다. 실행하지 않은 검사와 아직 측정하지 않은 수치를 완료로 표시하지 않는다.

GitHub/Jira 게시, 담당자 변경, 이슈 완료 처리, 배포는 사람의 요청 범위에서 수행한다. 코드가 있다는 이유만으로 Jira 이슈를 완료 처리하지 않는다.
