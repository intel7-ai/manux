"""Dan 00f8bda's optional static-pose/TCN fusion; no gesture or OS ownership here.

Weights and thresholds retain the source branch's meaning. This experiment is opt-in;
importing a model does not enable it or turn training-set scores into an evaluation.
"""
import numpy as np

STATIC_POSTURES = frozenset({"thumb_index", "thumb_middle", "thumb_ring", "thumb_pinky",
    "thumb_index_middle", "palm_side", "v_sign", "rock_sign", "thumbs_up"})


def blend(tcn, classes, spatial, spatial_classes, *, alpha=.42):
    out = np.asarray(tcn, dtype=np.float64).copy()
    sp = np.asarray(spatial, dtype=np.float64)
    if (out.shape != (len(classes),) or sp.shape != (len(spatial_classes),)
            or not np.isfinite(out).all() or not np.isfinite(sp).all()
            or (out < 0).any() or (sp < 0).any()
            or not np.isclose(out.sum(), 1.) or not np.isclose(sp.sum(), 1.)):
        raise ValueError("invalid hybrid probabilities")
    tmap, smap = {c:i for i,c in enumerate(classes)}, {c:i for i,c in enumerate(spatial_classes)}
    for label in STATIC_POSTURES.intersection(tmap, smap):
        ti, si = tmap[label], smap[label]
        pt, ps = out[ti], sp[si]
        value = (1-alpha)*pt + alpha*ps
        if ps >= .85 and pt >= .35:
            value = max(value, .78*ps + .22*pt)
        if "none" in smap and sp[smap["none"]] >= .80 and ps < .15 and pt < .88:
            value *= .35
        out[ti] = value
    if "none" in tmap:
        others = [v for label,v in zip(classes,out) if label != "none"]
        if others:
            out[tmap["none"]] = np.clip(1-max(others), 0., 1.)
        if out.sum() > 1e-8:
            out /= out.sum()
    return out


def instant_decision(label, tcn, classes, spatial, spatial_classes, threshold):
    """None = use temporal confirmation, False = neutral veto, True = agreeing fast path."""
    if label not in STATIC_POSTURES or label not in classes or label not in spatial_classes:
        return None
    pt, ps = float(tcn[classes.index(label)]), float(spatial[spatial_classes.index(label)])
    if "none" in spatial_classes and spatial[spatial_classes.index("none")] >= .80 and ps < .15 and pt < .88:
        return False
    if ps >= .85 and pt >= max(.32, threshold*.5):
        return True
    return None
