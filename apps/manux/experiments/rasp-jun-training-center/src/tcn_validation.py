"""Reject collapsed training runs before replacing even a trial model route."""


def publication_issues(info):
    issues = []
    for label in info["classes"]:
        if label == "none":
            continue
        fit = info.get("train_accuracy", {}).get(label)
        score = info.get("per_gesture", {}).get(label, {})
        if fit is not None and fit < .5:
            issues.append(f"{label}: 학습 데이터에서도 구분률 {fit:.0%} (최소 50%)")
        if score.get("total", 0) >= 10 and score.get("rate") is not None and score["rate"] < .25:
            issues.append(f"{label}: 제외 자세 분류율 {score['rate']:.0%} (최소 25%)")
    return issues
