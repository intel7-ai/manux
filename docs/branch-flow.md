# 브랜치 흐름: preview → production

2026-11-02 결정. 리뷰어 승인 대신 **preview에서 먼저 시험한 뒤 production으로 병합**한다.

```
작업 브랜치 (codex/<이슈>-<설명>)
   │ PR · MANUX required 통과 · 리뷰어 승인 불필요
   ▼
preview  ── 병합된 코드로 실제 앱 시험 (웹캠·Windows 입력·설치본)
   │ PR (head = preview) · MANUX required 통과 · 리뷰어 승인 불필요
   ▼
production  ── 배포 기준
```

## 규칙

1. 새 작업 브랜치는 최신 `origin/preview`에서 만들고 PR 대상도 `preview`로 한다.
2. `preview` PR은 **MANUX required**만 통과하면 작성자가 직접 병합할 수 있다. 다른 사람의 승인은 필요 없다.
3. `preview`에 병합한 뒤 그 상태로 실제 앱을 시험한다. 시험한 내용과 결과(실물/합성 구분, 미실행 항목)를 PR이나 Jira 작업 기록에 남긴다.
4. `production`에는 **`preview` 브랜치에서 연 PR만** 병합한다. 다른 브랜치에서 production으로 연 PR은 MANUX required의 마지막 단계가 실패시킨다.
5. `preview → production` PR 본문에는 포함된 PR 목록과 preview에서 시험한 결과를 적는다. 시험하지 않은 변경이 섞여 있으면 병합하지 않는다.
6. 두 브랜치 모두 직접 push·강제 push·삭제를 하지 않는다. 충돌은 작업 브랜치에서 해결한다.
7. production 병합은 production에 병합 커밋을 하나 만든다. 병합 직후 `origin/production`에서 작업 브랜치를 만들어 preview로 PR을 열어 되돌려 받는다. 그러지 않으면 preview가 production보다 뒤처져 다음 `preview → production` PR이 "최신 기준 브랜치" 검사에 막힌다.

## GitHub 설정 (관리자)

| 브랜치 | PR 필수 | 필요 승인 | 필수 검사 | 관리자 포함 | 강제 push·삭제 |
| --- | --- | --- | --- | --- | --- |
| `preview` | 예 | 0 | MANUX required (최신 기준 브랜치 필요) | 예 | 금지 |
| `production` | 예 | 0 | MANUX required (최신 기준 브랜치 필요) | 예 | 금지 |

승인이 없어졌으므로 실수를 막는 장치는 자동 검사와 preview 시험뿐이다. preview 시험을 건너뛰지 않는다.
