from __future__ import annotations

from .base import BaseProfiler, ProfilerResult


class DummyProfiler(BaseProfiler):
    def collect(self, run_context):
        return ProfilerResult(available=False, warnings=["Dummy profiler is used in V1."])

