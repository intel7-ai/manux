# 독립 MANUX 브랜치 흐름

전환 시각은 **2026-11-07 10:00 KST**다. 개발 기준은 `manux-preview`, 릴리스 브랜치는 `manux-production`이다. 원본 preview의 `apps/manux/`를 subtree split한 결과가 독립 브랜치의 저장소 루트다. 이후 명령에 `apps/manux/` 접두사를 붙이지 않는다.

## 개발과 정식 반영

| 순서 | 할 일 | 조건 |
| --- | --- | --- |
| 1 | 최신 `origin/manux-preview`에서 `<이슈>-<설명>` 작업 브랜치 생성 | 도구 접두사 금지, 진행 중 변경 보존 |
| 2 | 작업 브랜치 push → `manux-preview` PR | 목표·계층/계약·자동/실물 검증·미실행 기록 |
| 3 | Preview 설치본 확인 후 병합 | 필수 검사 **MANUX required** 통과, 리뷰어 승인 불필요 |
| 4 | `manux-preview → manux-production` PR | 같은 저장소의 해당 head만 허용, 설치본 확인 후 병합 |
| 5 | `manux-production` push에서 release.yml 실행 | 기본 정식 설치본 검증 후 publish, 태그 `v0.5.<run_number>` |

PR 병합은 **squash를 권장**한다. 다른 작업/원본 브랜치의 불필요한 이력이 들어오지 않게 한다. 릴리스 승격 PR도 같은 출처 규칙과 검사를 지킨다. 브랜치 비교에서 이미 반영된 커밋이 다시 보이면 실제 diff와 기준을 확인하며 강제 push로 이력을 맞추지 않는다. 보호 브랜치에 직접 push하거나 필수 검사를 우회하지 않는다.

## 자동 검사와 실물 검사

`standalone.yml`은 `manux-preview`·`manux-production` 대상 PR과 두 브랜치 push, 수동 실행을 받는다. Windows 전체 pytest, 구조·계약 시험, 기본 Preview 빌드·설치·자가 진단의 성공을 마지막 **MANUX required** job이 집계한다. 선행 job의 실패·취소·skip은 필수 집계 성공으로 바꾸지 않는다. `manux-production` PR에서는 head 이름뿐 아니라 원본 저장소도 검사한다.

보호 설정에는 검사 이름을 정확히 `MANUX required` 하나로 지정한다. 리뷰어 승인은 요구하지 않는다. CI가 성공해도 실제 웹캠·Windows 입력·사용자 독립 정확도가 확인된 것은 아니다. PR artifact의 설치본으로 요청한 실물 동작을 확인한 뒤 병합한다. 실행하지 않은 검사는 그대로 기록한다.

`release.yml`은 `manux-production` push에만 반응한다. build job은 읽기 권한으로 시험·`build.ps1 -Release`·번들 자가 진단을 수행하고 검증된 artifact ID를 publish job에 넘긴다. publish에만 `contents: write`를 주며 같은 저장소·릴리스 브랜치 조건에서 GitHub Release를 발행한다. PR이나 수동 이벤트로 정식 발행하지 않는다.

## 전환할 때 사람이 할 일

1. 준비 PR과 필요한 앱 PR을 preview에 반영한 뒤 분리 결과를 확인한다. 이 준비 PR은 기존 원본 `preview` 대상이며 독립 앱의 코드·UI를 바꾸지 않는다.
2. `manux-preview`의 분리 파일·검사 결과를 확인하고 기본 브랜치를 `manux-preview`로 바꾼다. `manux-production` 생성과 브랜치 보호 설정도 사람이 수행한다. release workflow가 포함된 커밋으로 릴리스 브랜치를 생성·push하면 정식 발행이 시작되므로, 첫 발행 준비와 순서를 확인한 뒤 수행한다. 이 준비 작업은 해당 설정을 변경하지 않는다.
3. 두 브랜치에 `MANUX required`를 필수 검사로 적용한다. 자동 split push는 전환 준비용이다. 독립 개발을 시작한 뒤 원본 preview와 독립 브랜치를 동시에 개발 기준으로 사용하지 않는다. 기존 split workflow가 분기 때문에 실패하면 강제 push하지 않고 사람에게 보고한다.

### 자동 분리 push의 권한 문제

이번 준비는 중첩 `.github/workflows/standalone.yml`·`release.yml`을 바꾸므로, **병합 후 자동 분리 push가 GitHub 기본 토큰 권한 때문에 실패할 수 있다. 그때는 사람이 분리 결과를 수동 push해야 한다.** workflow 변경을 push할 수 있는 자격 증명을 사용하되 권한을 임의로 늘리거나 토큰 값을 기록하지 않는다.

원본 preview 작업 폴더에서 아래처럼 최신 원본을 분리하고 fast-forward로만 올린다. 아래 예시는 사람이 실행하는 복구 절차이며 이 준비 PR은 분리 결과를 원격에 직접 올리지 않는다.

```powershell
git fetch origin
$splitCommit = git subtree split --prefix=apps/manux origin/preview
if ($LASTEXITCODE -ne 0) { throw 'subtree split 실패' }
git push origin "${splitCommit}:refs/heads/manux-preview"
if ($LASTEXITCODE -ne 0) { throw '수동 push 실패: 권한과 분기 여부를 확인하고 강제 push하지 마세요' }
```

일반 push가 non-fast-forward로 거절되면 멈추고 양쪽 이력을 검토한다. `--force`·`--force-with-lease`는 사용하지 않는다. 또한 `GITHUB_TOKEN`으로 성공한 자동 push는 새 push workflow를 시작하지 않는다. 사람이 일반 push 또는 해당 독립 브랜치의 수동 CI 실행으로 검사를 시작해야 한다. 발행은 이와 별도로 `manux-production`의 push 흐름을 따른다.
