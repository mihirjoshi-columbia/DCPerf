# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""Backwards-compatibility shim.

The real ``perf_sampler`` module now lives in ``packages/common/`` so
``video_transcode_bench``, ``wdl_bench``, and ``feedsim`` can share it.
Any importer that still does ``from perf_sampler import ...`` from
``packages/tao_bench/`` keeps working through this re-export.
"""

import os
import sys

_COMMON_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "common")
if _COMMON_DIR not in sys.path:
    sys.path.insert(0, _COMMON_DIR)

# Re-export the public surface unchanged.
from perf_sampler import (  # noqa: E402,F401
    DEFAULT_EVENTS,
    PerfSampler,
    perf_csv_path_for_instance,
    resolve_events,
)
