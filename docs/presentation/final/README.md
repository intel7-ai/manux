# MANUX 최종 발표자료

2026년 11월 13일 수정한 21장 발표와 후속 벤치마크 계획을 보관한다. 앱 코드와 릴리스 변경은 포함하지 않는다. 상위 폴더의 이전 발표자료 보관 안내와는 별개의 현재 자료다.

## 파일

- [index.html](index.html): 최종 발표 21장. 발표 메모, 출처, 장표 선택, Play Pad, UI 전환 애니메이션을 포함한다.
- [spatial-mlp.html](spatial-mlp.html): Spatial MLP와 Guard 설명. 발표 안에서 iframe으로 연다.
- [benchmark-plan.html](benchmark-plan.html): 6개 벤치마크의 가설·비교 조건·절차·지표와 결과 기록 양식. 스타일과 스크립트를 내장한 단일 HTML이므로 이 파일만 따로 공유할 수 있다.
- `assets/`: 발표에서 실제 참조하는 47개 이미지·SVG·동작 설명 GIF. 사용하지 않는 과거 자산은 포함하지 않는다.
- [evidence/metrics-summary.json](evidence/metrics-summary.json): 장표에 인용한 측정 수치의 조건과 해석 범위.
- [evidence/selected-worker-benchmark.json](evidence/selected-worker-benchmark.json): 합성 데이터로 실행한 CPU/CUDA 학습 worker의 기존 측정 기록. 원시 녹화나 개인 모델은 포함하지 않는다.

## 열기

저장소 루트에서 다음을 실행한 뒤 브라우저에서 안내한 주소를 연다. Python 표준 라이브러리만 사용한다.

```powershell
python -m http.server 8793 --bind 127.0.0.1 --directory docs/presentation/final
```

- 발표: <http://127.0.0.1:8793/index.html>
- 벤치마크 계획: <http://127.0.0.1:8793/benchmark-plan.html>

발표의 키보드·마우스 조작은 하단 도움말에서 확인한다. 양옆 영역 클릭과 방향키로 이동할 수 있다. 발표를 다른 곳으로 옮길 때는 `index.html`, `spatial-mlp.html`, `assets/`의 상대 위치를 유지한다.

벤치마크 계획은 파일을 브라우저에서 직접 열어도 외부 리소스 없이 읽을 수 있다. 체크리스트 상태는 파일에 저장되지 않는다. 인쇄 스타일은 포함했으며 실제 인쇄 미리보기·PDF 출력은 미확인이다.

## 반영한 내용과 범위

- 대상 사용 상황과 현재 지원 가능한 손 입력 범위, 향후 접근성 목표를 구분했다.
- 기술 스택에 Python·OpenCV·NumPy·ONNX Runtime·Qt/QML을 표시했다.
- UI 화면이 여러 방향에서 모이고, 흰 점과 소등 전환을 거쳐 Light UI가 확대되는 애니메이션을 포함한다.
- 손 추적 설정과 TCN 학습 worker의 기존 전후 수치를 측정 조건과 함께 제시했다.
- 실제 대상 사용자의 과제 성공·오입력·완료 시간·불편감 검증은 후속 계획으로 표시했다.

이 폴더의 발표는 공개 사이트 버전 6의 정적 소스를 기준으로 한다. 렌더링에 쓰이지 않는 PR 작성자 계정 필드 136개를 제거하고, 로컬에만 있던 worker 측정 근거 경로를 이 폴더의 기록으로 바꿨다. 발표의 시각 자료와 구조는 유지했다.

공개 발표: <https://manux-presentation.burblingcity2.chatgpt.site>

## 수치 해석

- 손 추적 31.451 → 14.760ms는 공식 정지 이미지를 반복 처리한 실행별 중앙값의 대표 비교다. 웹캠 FPS·손짓→화면 반응 지연이나 사용자 체감 개선률이 아니다. [측정 문서](../../../apps/manux/docs/cursor-performance.md)
- TCN worker 10.1046 → 6.7315초는 합성 데이터를 사용한 CPU/CUDA 각 3회 측정의 중앙값이다. 평가·내보내기/설치까지 포함하는 worker 범위이며, 복제 프레임은 독립 시도가 아니다. [기존 측정 기록](evidence/selected-worker-benchmark.json)
- MLP 17,689표본·98.84%·1.39초는 11월 9일 저장 모델의 내부 평가 기록이다. 독립 사용자 정확도나 Rules 대비 개선율을 뜻하지 않는다. 이 폴더에는 수치 요약과 원본 메타데이터 해시만 보관하며 개인 모델 파일은 포함하지 않는다.
- 이번 커밋에서 새 사용자 실험이나 실제 웹캠·Windows 성능 벤치를 수행한 것은 아니다. [후속 실험 계획](benchmark-plan.html)을 따라 독립 평가가 필요하다.

## 자산과 로컬 자료

발표의 출처 패널·메모·해당 장표에 외부 시각 자료 출처가 있다. Jumpingjacks.gif는 Wensceslao, CC BY-SA 4.0, 원본 사용으로 표시했다. 기타 자료의 출처와 조건도 함께 유지한다. 이 발표 전체에 새 라이선스를 부여하는 문서는 아니다.

로컬 검토 서버, 댓글 JSON, 수정 스크립트, 백업, 배포 설정·자격 증명, 생성 캐시는 커밋하지 않는다. 공개 사이트의 배포·앱 production 통합 상태는 이 Git 브랜치와 별개다.
