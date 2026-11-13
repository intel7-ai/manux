# MANUX Command r7

버전: `0.5.0-command.20261113.7` · 2026-11-13

이 브랜치는 별도 폴더에서 제작하고 검증한 MANUX Command r7의 소스 스냅샷입니다.
기존 실행본과 같은 동작을 보존하기 위해 원본 기반 커밋
`f1a88eea6eb17fa158d84d007fb7d701d5721cb2`에서 분기했습니다.
기존 개발·배포 브랜치에 병합하거나 해당 브랜치의 커밋을 수정하지 않습니다.

## 포함된 기능

- MANUX Command 화면, 라이트·다크 테마 선택, 제어 시작/정지에 따른 창 전환.
- 기존 AI 동작 등록·명령 연결, PC 명령·PowerShell·매크로 연결.
- 학습센터의 AI 동작 목록 즉시 반영, 연속/단일 표본 수집, 저장, 실제 MLP 학습·검증·적용.
- 현재 규칙·MLP 예측과 확신도·최종 판정·일치/불일치 표시 및 불일치 표본만 수집.
- 이전 기본 모델의 Rock 분류가 정상 클릭을 차단하던 경로 수정. 명시적으로 등록한 Rock 기반 AI 동작은 유지.

## r7 수정 범위

1. 포즈 표본에서 별명 입력칸에 포커스가 있어도 Space로 수집을 시작·정지합니다.
2. AI 연속 수집 중 손 소실·화면 이탈·불완전 관절은 표본 추가만 대기하고, 유효한 손이 돌아오면 같은 수집 시도를 이어갑니다.

수동 정지, 탭 전환, 창 닫기, 대상 삭제, 수집 상한에 따른 종료는 유지합니다.
r6 대비 실행 코드 변경은 위 두 문제와 관련된 세 파일뿐입니다.
기존 사용자 모델·표본·명령 연결은 변경하지 않습니다.

## 검증

- 전체 회귀 검사: 1,474개 통과, 11개 건너뜀.
- Windows Qt 창의 키 입력·수집 상태 검사: 16개 통과.
- EXE 시작, 번들 의존성, 학습 워커 자가 진단 및 공유 ZIP 파일별 해시 검증 완료.
- 손 소실·복귀는 합성 관절 입력으로 검증했습니다. 실제 웹캠 인식률, 다른 물리 PC, 실제 종료 명령, 온라인 AI 요청은 이번 검증 대상이 아닙니다.

검증 정보는 [command-r7-validation.json](command-r7-validation.json),
빌드 메타데이터는 [command-r7-build.json](../packaging/command-r7-build.json)에 있습니다.
ZIP·EXE·개인 표본·API 키·로컬 설정은 Git 커밋에 포함하지 않습니다.

## 빌드

저장소의 기존 개발 환경 설치 방법을 따른 후 Windows PowerShell에서 실행합니다.

```powershell
pwsh -NoProfile -File packaging/build.ps1 -Version 0.5.0-command.20261113.7 -WithTrainer -SkipPack -DataProfile MANUX-Command/v05-command-20261112
```

인터넷 카메라용 cloudflared를 함께 배포할 때는 검증한 실행 파일을
`-CloudflaredPath <cloudflared.exe 경로>`로 지정합니다.
설치된 Python 환경을 직접 사용하려면 `-PythonExecutable <python.exe 경로>`를 지정합니다.
빌드 결과의 `Manux.exe --ui studio`가 Command 화면을 엽니다.
기존 공유본의 사용자 데이터 프로필을 사용하며 자동 업데이트는 꺼져 있습니다.
이 문서의 빌드 명령은 새 빌드를 만들며, 기록된 ZIP 해시는 이미 검증된 r7 배포 파일의 해시입니다.
