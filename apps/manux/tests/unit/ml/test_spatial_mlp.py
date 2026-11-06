"""통합 MLP: 평가 무분할과 Studio 저장/로드를 함께 보존한다."""
import numpy as np
import pytest
from manux.ml.spatial_mlp import SpatialMlpModel, train_spatial_mlp

@pytest.mark.parametrize("dims,version", [(33,"pose-angles-v1"),(96,"spatial-mlp-v1")])
def test_dynamic_classes_persist_without_internal_frame_split(tmp_path, dims, version):
    x=np.random.default_rng(0).normal(size=(20,dims)).astype(np.float32)
    y=np.array([0]*10+[1]*10)
    model,metrics=train_spatial_mlp(x,y,classes=("none","custom_pose"),feature_version=version,epochs=2)
    assert metrics.train_samples==20 and metrics.test_samples==0 and metrics.test_accuracy is None
    loaded=SpatialMlpModel.load(model.save(tmp_path/'model.json'))
    assert loaded.classes==model.classes and loaded.feature_version==version and loaded.feat_dim==dims
    np.testing.assert_allclose(loaded.predict_proba(x),model.predict_proba(x),atol=1e-5)

def test_external_check_samples_never_enter_optimizer():
    x=np.zeros((8,33),dtype=np.float32);y=np.array([0,1]*4)
    _,metrics=train_spatial_mlp(x,y,classes=("none","custom"),feature_version="pose-angles-v1",epochs=1,
                                validation_data=(x[:2],y[:2]))
    assert metrics.train_samples==8 and metrics.test_samples==2 and metrics.test_accuracy is not None
