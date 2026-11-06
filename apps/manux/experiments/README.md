# testing 실험 소스

현재 MANUX 실행·설치 경로는 `apps/manux/src/manux/`다. 이 폴더는 testing에서 비교하고 이식할
소스를 보존한다. 기본 앱 import, PyInstaller 패키징 및 기본 pytest 대상에 넣지 않는다.

`dan-overhaul/`은 `hand-mouse-gesture-overhaul@b3c3025`의 소스 사본이다. 원본 파일별 해시는
`dan-overhaul/UPSTREAM-SOURCE.json`에 있다. 녹화·실패 로그·개인 모델·인증·로컬 프로필과
녹화 기반 fixture는 가져오지 않았다. 따라서 원본 앱의 모든 실행·검사를 재현한 배포본이 아니다.

이 소스를 현재 앱에 연결할 때는 앱 지침·구조·계약을 먼저 읽고 v0.4 계층으로 기능별 이식한다.
여기에 있던 과거 안내·CLAUDE 지침을 현재 제품의 지침으로 쓰지 않는다. 기본 검색에서 제외하며
조사할 때 `rg --no-ignore PATTERN apps/manux/experiments/dan-overhaul/`로 명시한다.
