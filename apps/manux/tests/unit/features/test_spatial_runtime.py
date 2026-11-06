from types import SimpleNamespace
import numpy as np
from manux.app import schema
from manux.desktop import build
from manux.core.contracts import RuntimeRequest
from manux.settings.store import Settings
from manux.ml.spatial_mlp import SpatialMlpModel


def test_spatial_guard_changes_only_at_runtime_boundary_and_survives_tcn_toggle(tmp_path):
    model=SpatialMlpModel(np.zeros((4,96)),np.zeros(4),np.zeros((2,4)),np.zeros(2),classes=("none","thumb_ring"))
    path=model.save(tmp_path/'pose.json')
    runtime=build(Settings(schema()),SimpleNamespace(close=lambda:None),windows=False)
    runtime.spatial_model_path=path
    try:
        assert not runtime.spatial_guard.enabled
        runtime.request(RuntimeRequest("spatial_guard","on"))
        assert not runtime.spatial_guard.enabled
        runtime._drain_runtime_requests()
        assert runtime.spatial_guard.enabled and runtime.spatial_guard in runtime.engine.filters
        runtime.request(RuntimeRequest("tcn_toggle","off"));runtime._drain_runtime_requests()
        assert runtime.spatial_guard in runtime.engine.filters
        runtime.request(RuntimeRequest("spatial_guard","off"));runtime._drain_runtime_requests()
        assert not runtime.spatial_guard.enabled and runtime.spatial_guard not in runtime.engine.filters
    finally:runtime.stop()
