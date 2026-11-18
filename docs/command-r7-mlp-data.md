# MANUX Command r7 MLP 데이터 스냅샷

2026-11-18 사용자의 명시적 요청으로 r7 브랜치에 개인 MLP 학습 데이터와 모델을 함께 보존합니다.
r7 앱 소스는 기존 커밋 6db7f4a8559d202d091b62c77d1231145ea0f1e0와 같습니다. 실행 중인 앱과 사용자 프로필은 변경하지 않았습니다.

## 포함 내역

- data/pose_snapshots/: 현재 r7 프로필의 포즈 표본 43개 파일을 병합했습니다. 기존 파일도 유지하여 총 54개입니다.
- data/legacy/: 기존 표본을 유지하고 r7 프로필과 대조했습니다.
- models/spatial_mlp/: 현재 선택 모델 gh.json와 모델 선택 정보를 포함합니다.
- 활성 모델 학습 시각: 2026-11-18 16:54:53. 모델에 기록된 학습 표본 수: 26,037.
- models/library/: 모델 보관함의 spatial MLP 모델과 메타데이터를 함께 포함합니다.
- data/command-r7-profile/ai-customization/data/posture_examples/: AI 동작 표본 52개 JSON 및 .negatives.npz 음성 표본 캐시를 포함합니다.
- data/command-r7-profile/ai-customization/models/: 학습된 AI 동작 MLP를 포함합니다.

정확한 파일 목록, 출처 상대 경로, 크기, SHA-256과 표본 수는
[command-r7-mlp-manifest.json](command-r7-mlp-manifest.json)에 있습니다.
manifest의 sha256은 원본 복사본 기준이고 git_blob_sha256은 Git에 저장된 바이트 기준입니다. Git의 줄바꿈 정규화 차이는 별도로 기록했습니다.
기존 표본은 삭제하지 않았으며, 학습·평가 구분과 시도 식별자는 원본 그대로 보존했습니다.

## 사용 방법

개발 소스 실행은 기존 경로 규칙에 따라 data/pose_snapshots/와 models/spatial_mlp/를 사용합니다.
MANUX_DATA_DIR를 지정하거나 EXE를 실행하면 별도 사용자 프로필을 사용합니다.
새로운 프로필에 이 스냅샷을 구성하려면 다음 경로로 파일을 복사합니다.

| 저장소 경로 | 사용자 프로필 내부 경로 |
| --- | --- |
| data/pose_snapshots/ | pose_snapshots/ |
| data/legacy/ | legacy/ |
| models/spatial_mlp/ | models/spatial_mlp/ |
| models/library/ | models/library/ |
| data/command-r7-profile/ai-customization/ | ai-customization/ |

기존 사용자의 프로필을 덮어쓰지 말고 별도 빈 프로필에서 확인하십시오.
동작 인식용 표본·가중치를 공유하며, 개인 AI 명령 연결과 API 인증 설정은 각 환경에서 구성합니다.
이 Git 데이터 갱신은 기존 EXE/ZIP을 다시 빌드하거나 교체한 것이 아닙니다.

## 검증

- 수집 시점의 파일 목록과 내용을 두 번 읽어 일관성을 확인했습니다.
- 선택한 MLP 파일 전부의 SHA-256을 복사 전후 대조했습니다.
- JSON 구문, 표본 수, AI 표본 관절 구조, NPZ 배열을 확인했습니다.
- Spatial MLP 20개와 AI MLP 1개를 r7 코드로 로드하고 추론 출력의 유효성을 확인했습니다.
- 모델 보관함 메타데이터의 모델 해시와 실제 파일을 대조했습니다.
- 인증정보 패턴과 로컬 사용자 절대 경로가 포함되지 않았는지 검사했습니다.
- 관련 회귀 검사 73개를 별도 임시 폴더와 offscreen Qt 환경에서 모두 통과했습니다. 최초 검사는 기존 시스템 임시 폴더 접근 오류로 49개 setup 오류가 발생했으며, 코드 변경 없이 새 임시 폴더에서 재실행해 통과했습니다.
- 실제 웹캠 정확도나 다른 사용자의 인식률을 새로 검증한 결과는 아닙니다.

원시 영상, API 키, 인증서 개인키, 네트워크 연결 정보, 작업 로그는 포함하지 않습니다.
