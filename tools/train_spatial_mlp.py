"""Train Spatial MLP model from pose snapshots.

Usage:
    uv run python tools/train_spatial_mlp.py [--model gh.json]
"""
import argparse
import sys
from pathlib import Path

# Add src to path if running directly
repo_root = Path(__file__).resolve().parent.parent
src_dir = repo_root / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from manux.desktop import make_pose_studio


def main() -> None:
    parser = argparse.ArgumentParser(description="Train Spatial MLP model from snapshots.")
    parser.add_argument("--model", default="gh.json", help="Model name or filename (default: gh.json)")
    args = parser.parse_args()

    data_dir = repo_root / "data"
    models_dir = repo_root / "models" / "spatial_mlp"
    models_dir.mkdir(parents=True, exist_ok=True)

    studio = make_pose_studio(data_dir)
    studio.models_dir = models_dir
    target = args.model if args.model.endswith(".json") else f"{args.model}.json"
    studio.active_model_id = target

    print(f"📦 표본 탐색 디렉터리: {data_dir / 'pose_snapshots'}")
    print(f"🎯 학습 대상 모델: {target}")

    res = studio.train_now(target)
    if not res.get("success"):
        print(f"❌ 학습 실패: {res.get('reason')}")
        sys.exit(1)

    print("\n✅ 학습 완료!")
    print(f"  - 저장 위치: {models_dir / target}")
    print(f"  - 총 표본 수: {res['total_samples']:,}개 (학습: {res['train_samples']:,}개, 검증: {res['test_samples']:,}개)")
    print(f"  - 훈련 정확도: {res['train_accuracy']}%")
    print(f"  - 검증 정확도: {res['test_accuracy']}%")
    print(f"  - 소요 시간: {res['elapsed_ms']:.1f}ms")
    print("\n📊 클래스별 성능:")
    for cls_name, info in res.get("per_class", {}).items():
        print(f"  - {cls_name:18s}: {info['accuracy']:5.1f}% ({info['correct']}/{info['total']})")


if __name__ == "__main__":
    main()

