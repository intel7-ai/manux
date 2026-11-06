# v0.5 스튜디오 통합

작업 시작 기준 preview 116c674에서 #95는 OPEN이었다. 작업 중 #95가 preview 8b6890c로 병합되어 이를 추가 병합했다. session_id 구현과 시험을 보존한다.

| 입력 | 고정 커밋 | 우선한 범위 |
|---|---|---|
| testing | 431f9b0 | Dan·Rasp-Jun 스튜디오, 데이터/모델 라이브러리, worker 진행·공유 |
| pose-assist-v1 (#82) | ac505c9 | 최신 96차원 MLP·Pose Studio·모델 선택/저장/즉시 재적용 |
| codex/dan-sensitivity-settings (#91) | e8e8b10 | 기존 스튜디오 민감도 설정과 런타임 반영 |

병합 순서는 위 표 순서다. experiments 전체와 testing에서 바뀐 저장소 루트 파일은 병합 트리에서 제외했다. 루트 run_manux.bat, docs/testing.md, docs/root-cleanup-audit.md, docs/product-direction.md와 testing 전용 CI/루트 README 변경을 가져오지 않았다. apps/manux 안의 실행기·모델·폰트는 앱 구성으로 포함한다. 상위 브랜치 이력은 병합 부모로 보존한다.

MLP는 ml/spatial_mlp.py 하나이며 평가용 33/96차원·동적 클래스·내부 무분할과 Studio 저장/로드를 함께 지원한다. eval_judges 시험 파일은 변경하지 않는다. 96차원 특징 파일은 #82 원본, pose_angles.py는 평가 비교용 원본이다.

Pose 내부 확인의 무작위 80/20 비율·작은 표본 재확인·정확도 산식은 유지하되, 분할 책임은 Studio 호출부로 옮겼다. 화면은 **학습 데이터 내부 확인(실제 정확도 아님)**이라고 표시한다. 세션/사람 독립 평가와 혼동하지 않는다. 기존 18개 TCN 과제는 Pose 화면과 별개로 유지한다.

Pose 서비스와 Dan 스튜디오 객체를 별도로 두며 저장/선택한 MLP는 런타임 큐의 프레임 경계에서 재적용한다. worker가 없는 기본 설치본의 TCN 고급 학습 차단은 유지한다. feature 간 import나 Qt·torch 계층 제한은 완화하지 않는다.

rock_sign 클래스는 spatial_pose.rock_sign 동작으로 바꾸었다. window.close 액션은 별도 바인딩이며 tap 때 Alt+F4를 누르고 역순 해제한다. 기본 assist 모드는 직접 명령을 내지 않는다. 실제 창 닫기 동작의 수동 검증은 별도다.

검증은 pytest 전체·tests/architecture·Qt 화면 시험·저장소 밖 subtree clone pytest·python -m manux --self-check로 구분한다. 설치본 크기는 PR의 실제 CI package-size.json 측정값을 따른다. 합성 시험/자가 진단은 실제 웹캠·Windows 사용성이나 인식률 검증이 아니다.

통합 CI에서 Trainer 번들의 창 검사가 한 번 90초 제한을 넘겼다. 원인 확인을 위해 screenshot 검사에만 60초 후 스레드 스택을 기록하고 CI가 격리된 임시 앱 데이터 폴더의 로그를 수집한다. 검사 제한 시간·성공 기준은 유지한다. 실제 재실행 결과와 재현 여부는 PR에 기록한다.

이 통합 PR이 preview에 병합되면 #82·#91·testing의 기능 통합을 대체한다. 해당 PR을 닫는 것은 사람이 수행한다. production 발행은 별도 preview → production PR 이후다.
