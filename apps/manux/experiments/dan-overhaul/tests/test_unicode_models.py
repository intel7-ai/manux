"""Regression for MediaPipe native path loading on Korean Windows workspaces."""
import shutil

import numpy as np

from src.hand_tracker import DEFAULT_MODEL_PATH, HandTracker
from src.pose_tracker import DEFAULT_POSE_MODEL, PoseTracker


def test_hand_and_pose_models_load_from_korean_folder(tmp_path):
    folder = tmp_path / "한글 모델 경로"
    folder.mkdir()
    blank = np.zeros((240, 320, 3), dtype=np.uint8)
    hand_path = folder / "손.task"
    pose_path = folder / "자세.task"
    shutil.copyfile(DEFAULT_MODEL_PATH, hand_path)
    shutil.copyfile(DEFAULT_POSE_MODEL, pose_path)
    tracker = HandTracker(model_path=hand_path)
    try:
        assert tracker.detect(blank) == []
    finally:
        tracker.close()
    pose = PoseTracker(model_path=pose_path)
    try:
        assert pose.detect(blank) is None
    finally:
        pose.close()
