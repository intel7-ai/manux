"""Compare a trained MANUX TCN and the recorded rules on separate evaluation recordings."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from manux.features.tcn_gate import gestures_for
from manux.ml.card import ModelError
from manux.ml.predictor import load_model
from manux.perception.local3d import FEATURES, FEATURE_VERSION
from tcn_data import evaluate, groups, load


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", type=Path)
    parser.add_argument("recordings", nargs="+", type=Path)
    parser.add_argument("--group-by", choices=("person", "posture"))
    args = parser.parse_args()
    try:
        model = load_model(args.model, FEATURE_VERSION, FEATURES)
        card = model.card
        segments = load(list(dict.fromkeys(path.resolve() for path in args.recordings)), split="eval")
        grouped = groups(segments, args.group_by) if args.group_by else {"all": segments}
        if not grouped:
            raise ValueError("no recordings have the requested grouping metadata")
        results = {}
        for group, rows in grouped.items():
            results[group] = evaluate(rows, list(card.classes), gestures_for(card),
                                      lambda x: np.stack([model.predictor(row) for row in x]),
                                      card.window, card.threshold, card.frames_in_a_row)
        print(json.dumps(results, indent=2))
    except (ValueError, OSError, ImportError, ModelError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
