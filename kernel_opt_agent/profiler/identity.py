"""Shared observation/evidence identity semantics, without alias precedence."""
from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any


COLLECTION_IDENTITY_FIELDS = (
    "collection_id", "source_trial_id", "source_sha256", "generated_sha256",
    "device_id", "shape_id", "dtype", "kernel_name", "launch_id",
    "tool_version", "tool_build", "gpu_model",
)
IDENTITY_ALIASES = MappingProxyType({
    **{name: (name,) for name in COLLECTION_IDENTITY_FIELDS},
    "metric": ("metric", "metric_name"),
    "source_field": ("source_field", "source_field_name"),
    "artifact_id": ("artifact_id", "artifact"),
    "source": ("source",),
})
IDENTITY_KEYS = frozenset(alias for aliases in IDENTITY_ALIASES.values() for alias in aliases)


def canonical_identity(row: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Resolve unique values; an explicit null/empty alias cannot hide a conflict.

    Absent fields and consistent nulls remain unknown. Values are not normalized,
    and diagnostics never include caller-supplied values.
    """
    scope = row.get("collection_scope")
    reasons = []
    if scope is None:
        scope = {}
    elif not isinstance(scope, Mapping):
        scope = {}
        reasons.append("collection_scope_invalid")
    identity = {}
    for name, aliases in IDENTITY_ALIASES.items():
        values = [container[alias] for container in (row, scope) for alias in aliases if alias in container]
        value = values[0] if values else None
        if any(type(other) is not type(value) or other != value for other in values[1:]):
            identity[name] = None
            reasons.append(name + "_identity_conflict")
        elif value is not None and (not isinstance(value, str) or not value.strip()):
            identity[name] = None
            reasons.append(name + "_identity_invalid")
        else:
            identity[name] = value
    return identity, reasons
