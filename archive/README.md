# 이전 앱 보관

현재 checkout에는 v0.4 MANUX와 정식 릴리스 유지보수용 v0.3만 남깁니다. v0.2 웹앱·Electron을 토큰과 검색 결과에 반복 노출하지 않도록 소스 트리에서 퇴역시켰습니다.

## 고정 복구 지점

[preview 589cc9a 전체 트리](https://github.com/intel7-ai/manux/tree/589cc9a8d338e40df8edc005825caf0c9a6329fc)에 다음이 함께 보존돼 있습니다.

- `src/`, `public/`, `desktop/`, `server.js`: v0.2 앱.
- 당시 `package.json`·잠금 파일·scripts·tests: 앱 의존성과 회귀 시험.
- `.cmd`·PowerShell 실행기·Electron 패키징 설정·소스 전달 workflow.
- 과거 계획·설계·발표 자료·Jira 사본. 기존 Markdown 주소는 짧은 보관 안내로 남겼습니다.

필요할 때 현재 작업 폴더 밖의 별도 경로에서 복구하세요.

```powershell
git worktree add --detach ..\manux-web-v02 589cc9a8d338e40df8edc005825caf0c9a6329fc
```

그 폴더에서 당시 README·패키지·검사를 사용합니다. 현재 checkout의 package.json과 섞지 않습니다. 이 커밋을 현재 제품 상태나 설치본으로 설명하지 않습니다.

`hand-mouse/`와 정식 릴리스 workflow는 아직 이 보관 대상이 아닙니다. v0.4 정식 배포 전환 전에 삭제하지 않습니다.
