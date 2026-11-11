"""
MANUX 손짓 학습 스튜디오 - 시작 메뉴 (2026-11-05).

studio.bat 이 이 메뉴를 띄운다. 긴 명령(uv run --locked --extra qt --extra train --extra llm python main.py --ui qt ...)을
외우지 않아도 번호만 고르면 되고, 고를 때마다 실제로 실행되는 명령과 각 옵션의 뜻을 보여 준다.
메뉴에서 고른 것은 지금 이 창의 Python(.venv, qt·train 묶음 설치됨)으로 실행한다.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PY = sys.executable

OPTIONS = {
    "--ui qt": "MANUX Qt 화면 (녹화·학습 센터·손짓 확인이 있는 화면)",
    "--source N": "카메라 번호 (0 = 첫 카메라. 폰을 카메라로 연결했다면 보통 0 또는 1)",
    "--phone-web": "휴대폰 브라우저를 카메라로 (QR로 접속, 앱 설치 없음)",
    "--headless": "창 없이 알림 영역(트레이)에서만 실행",
    "--profile 이름": "보정 프로필 고르기",
}

MENU = [
    ("1", "앱 실행 (연결된 카메라)", "마우스 조작 + 학습 데이터 녹화 + 학습 센터 + 손짓 확인(TCN)"),
    ("2", "앱 실행 (휴대폰 브라우저 카메라)", "휴대폰으로 QR 접속해서 카메라로 씀"),
    ("3", "학습 센터 리포트", "동작별 데이터 양·인식률·오작동과 '어떤 동작을 더 녹화할지' 추천"),
    ("4", "TCN 학습하기", "recordings/ 의 녹화로 학습 (GPU 가속 가능 여부 자동 감지 · GPU/CPU 선택)"),
    ("5", "모델 내보내기", "다른 앱에 넣을 수 있는 묶음 만들기 (exports/)"),
    ("6", "허브 설정 및 상태 확인", "저장소 주소(Hugging Face / GitHub)·보안 토큰(DPAPI 암호화) 설정 및 현황 확인"),
    ("7", "허브에 업로드 (push)", "검증된 새 녹화(.jsonl.gz)와 TCN 모델을 설정된 원격 허브에 업로드"),
    ("8", "허브에서 받기 (pull)", "팀원들이 올린 새 녹화와 모델을 설정된 원격 허브에서 내려받기"),
    ("9", "시험 돌리기", "pytest: 앱이 망가지지 않았는지 확인"),
    ("0", "끝내기", ""),
]


def _prompt_hub_setup_if_needed(require_token: bool = False) -> None:
    from tools import hub as hf_hub
    import getpass

    cfg = hf_hub.load_hub_config()
    tok = hf_hub.load_hub_token(cfg["provider"])
    prov_label = "GitHub" if cfg["provider"] == "github" else "Hugging Face"
    tok_status = f"암호화 저장됨 ({hf_hub.mask_token(tok)})" if tok else "미설정"
    print(f"\n  [현재 원격 허브 설정] 플랫폼: {prov_label} | 저장소: {cfg['repo']} ({cfg['branch']}) | 토큰: {tok_status}")

    change = input("  허브 주소나 토큰을 변경/입력하시겠습니까? (y/엔터=그대로 진행): ").strip().lower()
    if change == "y" or (require_token and not tok):
        if require_token and not tok and change != "y":
            print("  ※ 업로드를 위해 액세스 토큰 입력이 필요합니다.")
        p_in = input(f"  플랫폼 선택 (1=Hugging Face, 2=GitHub, 엔터={prov_label}): ").strip()
        provider = "github" if p_in == "2" else ("huggingface" if p_in == "1" else cfg["provider"])
        r_in = input(f"  저장소 주소 (owner/repo 또는 전체 URL, 엔터={cfg['repo']}): ").strip() or cfg["repo"]
        b_in = input(f"  브랜치 이름 (엔터={cfg['branch']}): ").strip() or cfg["branch"]
        try:
            t_in = getpass.getpass("  액세스 토큰 입력 (화면에 표시되지 않음, 엔터=기존 유지): ").strip()
        except Exception:
            t_in = input("  액세스 토큰 입력 (엔터=기존 유지): ").strip()
        saved = hf_hub.save_hub_config(provider=provider, repo=r_in, branch=b_in, token=t_in if t_in else None)
        new_tok = hf_hub.load_hub_token(saved["provider"])
        print(f"  ✓ 저장 완료: {saved['provider']} · {saved['repo']} ({saved['branch']}) · 토큰 {hf_hub.mask_token(new_tok) or '없음'}")


def run(args, explain: str, python: str | None = None) -> None:
    shown = " ".join(["python"] + [a if " " not in a else f'"{a}"' for a in args])
    print(f"\n  ▶ 실행: uv run --locked --extra qt --extra train --extra llm {shown}")
    print(f"    ({explain})\n")
    subprocess.run([python or PY] + args, cwd=ROOT)
    input("\n  [엔터]를 누르면 메뉴로 돌아갑니다 ")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    while True:
        print("\n" + "=" * 60)
        print("  MANUX 손짓 학습 스튜디오")
        print("  녹화 → 학습 → 성적 확인 → 부족한 동작 집중 녹화 → 다시 학습 → 내보내기")
        print("=" * 60)
        for key, title, desc in MENU:
            print(f"  {key}. {title}" + (f"\n       - {desc}" if desc else ""))
        print("\n  옵션 뜻:")
        for opt, desc in OPTIONS.items():
            print(f"    {opt:16s} {desc}")
        choice = input("\n  번호를 고르세요: ").strip()
        if choice == "0":
            return 0
        if choice == "1":
            cam = input("  카메라 번호 (엔터 = 0): ").strip() or "0"
            run(["main.py", "--ui", "qt", "--source", cam], f"카메라 {cam}번으로 Qt 화면 실행")
        elif choice == "2":
            run(["main.py", "--ui", "qt", "--phone-web"], "휴대폰 브라우저 카메라로 Qt 화면 실행")
        elif choice == "3":
            run(["scripts/training_report.py"], "녹화 데이터와 최근 학습 결과를 동작별로 요약")
        elif choice == "4":
            from src.training_center import detect_gpu_training_capability, set_training_device, training_python
            cap = detect_gpu_training_capability()
            print(f"\n  [연산 장치 감지] {cap['status_label']}")
            dev_flag = "cpu"
            if cap.get("cuda_available"):
                d_in = input(f"  학습 연산 장치 선택 (1=GPU 가속 [{cap.get('device_name')}], 2=CPU, 엔터=1): ").strip()
                dev_flag = "cpu" if d_in == "2" else "gpu"
            else:
                print("  → 현재 PC에서 CUDA GPU를 사용할 수 없어 CPU 학습으로 진행합니다.")
            set_training_device(dev_flag)
            run(
                ["scripts/train_tcn.py", "--folds", "--resume", "--device", dev_flag],
                f"{dev_flag.upper()}로 자세별 교차평가 및 최종 학습 후 models/gesture_tcn.onnx 저장",
                python=training_python(),
            )
        elif choice == "5":
            run(["scripts/export_model.py"], "exports/ 에 모델·특징 코드·사용 예시·설명서를 묶어 zip으로")
        elif choice == "6":
            _prompt_hub_setup_if_needed(require_token=False)
            run(["tools/hub.py", "status"], "로컬 녹화 무결성 검사와 원격 데이터 허브 요약")
        elif choice == "7":
            _prompt_hub_setup_if_needed(require_token=True)
            model_args = ["--model", "models/gesture_tcn.onnx"] if (ROOT / "models" / "gesture_tcn.onnx").is_file() else []
            run(["tools/hub.py", "push"] + model_args, "새 녹화와 모델을 검증 후 설정된 원격 허브에 업로드")
        elif choice == "8":
            _prompt_hub_setup_if_needed(require_token=False)
            run(["tools/hub.py", "pull", "--models"], "원격 허브에서 없는 녹화와 공유 모델 내려받기")
        elif choice == "9":
            run(["-m", "pytest", "-q"], "전체 시험")
        else:
            print("  0~9 중에서 골라 주세요.")


if __name__ == "__main__":
    raise SystemExit(main())
