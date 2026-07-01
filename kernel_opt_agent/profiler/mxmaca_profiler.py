from __future__ import annotations

from .base import BaseProfiler, ProfilerResult


class MxmacaProfiler(BaseProfiler):
    def collect(self, run_context):
        return ProfilerResult(available=False, warnings=["TODO: mxmaca profiler integration is reserved for a future version."])

