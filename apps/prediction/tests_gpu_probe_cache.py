"""Regression: the GPU-probe cache must not hand one model's verdict to another.

``_lightgbm_gpu_predict_cache_key`` falls back to ``id(model)`` when the model has no
``_handle``. CPython reuses addresses once an object is collected, so a collected model's
cached verdict used to be returned for a different model that landed on the same address.
That surfaced as a CUDA-fallback test that passed on its own and failed in full-suite
order, where the extra tests shift which objects are allocated when.
"""
from unittest.mock import patch

import numpy as np
from django.test import SimpleTestCase

from apps.prediction import tasks_lightgbm


class _Booster:
    def __init__(self, supported=()):
        self.supported = set(supported)

    def predict(self, matrix, **kwargs):
        device = kwargs.get('device_type')
        if device is not None and device not in self.supported:
            raise RuntimeError(f'unsupported device: {device}')
        return np.zeros((len(matrix), 3), dtype=np.float64)


class GpuProbeCacheTests(SimpleTestCase):
    def test_models_without_a_booster_handle_are_never_served_a_cached_verdict(self):
        first = _Booster({'cuda'})
        second = _Booster()
        sample = np.zeros((1, 3), dtype=np.float64)

        # Force both models onto one cache key: exactly what an address collision does.
        with patch.object(
            tasks_lightgbm,
            '_lightgbm_gpu_predict_cache_key',
            return_value=('forced-collision',),
        ):
            self.assertEqual(
                tasks_lightgbm._probe_lightgbm_gpu_predict_device(first, sample), 'cuda'
            )
            self.assertIsNone(
                tasks_lightgbm._probe_lightgbm_gpu_predict_device(second, sample),
                'a handle-less model must be probed, not served another model\'s verdict',
            )

    def test_a_real_booster_handle_is_still_cached(self):
        class Handled(_Booster):
            def __init__(self, supported=()):
                super().__init__(supported)
                self._handle = object()

        model = Handled({'cuda'})
        sample = np.zeros((1, 3), dtype=np.float64)

        self.assertEqual(
            tasks_lightgbm._probe_lightgbm_gpu_predict_device(model, sample), 'cuda'
        )
        key = tasks_lightgbm._lightgbm_gpu_predict_cache_key(model)
        self.assertIn(key, tasks_lightgbm._LIGHTGBM_GPU_PREDICT_DEVICE_CACHE)
        tasks_lightgbm._LIGHTGBM_GPU_PREDICT_DEVICE_CACHE.pop(key, None)
