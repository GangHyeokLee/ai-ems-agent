"""Per-native-object access and conservative, process-local model identity.

No calculation, preparation run, API envelope or result registry lives here.
Raw native access outside these boundaries is reserved for trusted callers that
hold ``network_access(network)`` themselves.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
import uuid
import weakref
import xml.etree.ElementTree as ET
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from functools import wraps
from typing import Any

import pypowsybl as pp


class NetworkBusy(RuntimeError):
    """Another thread owns this native network; no native access was attempted."""


class ModelIdentityUnavailable(RuntimeError):
    """The actual loaded model/settings could not be identified."""


class ModelRevisionChanged(RuntimeError):
    """A previously captured working-state revision is no longer current."""


EXPORT_PARAMETERS = {
    "iidm.export.xml.version": "1.17",
    "iidm.export.xml.sorted": "true",
    "iidm.export.xml.indent": "false",
    "iidm.export.xml.with-branch-state-variables": "true",
    "iidm.export.xml.with-automation-systems": "true",
    "iidm.export.xml.anonymised": "false",
    "iidm.export.xml.topology-level": "NODE_BREAKER",
    "iidm.export.xml.topology-level.voltage-levels.node-breaker": "",
    "iidm.export.xml.topology-level.voltage-levels.bus-breaker": "",
    "iidm.export.xml.topology-level.voltage-levels.bus-branch": "",
    # The exporter rejects specifying inclusion AND exclusion, even if empty.
    # An explicitly empty exclusion list preserves every supported extension.
    "iidm.export.xml.excluded.extensions": "",
    "iidm.export.xml.flatten": "false",
    "iidm.export.xml.only-main-cc": "false",
    "iidm.export.xml.only-selected-operational-limits-groups": "false",
    "iidm.export.xml.throw-exception-if-extension-not-found": "true",
    "iidm.export.xml.force-export-network-with-beta-features": "false",
}
_creation_lock = threading.Lock()


def _normalize(value: Any) -> Any:
    if value is None or type(value) in (str, int, float, bool):
        return value
    if isinstance(value, dict):
        return {str(key): _normalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    if hasattr(value, "name"):
        return {"enum": type(value).__name__, "name": value.name}
    return {"type": type(value).__name__, "fields": _normalize(vars(value))}


def _digest(value: Any) -> str:
    encoded = json.dumps(_normalize(value), sort_keys=True, ensure_ascii=False,
                         allow_nan=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class ModelRevision:
    """Internal metadata, never evidence of a successful physical calculation."""

    revision: str
    generation: str
    state_sha256: str
    settings_sha256: str
    initial_state: bool


class NetworkAccess:
    def __init__(self, network):
        self._network = weakref.ref(network)
        self._lock = threading.RLock()
        self._provenance = None
        self._parameters = None
        self._providers = None
        self._initial_state = None
        self._generation = None

    @contextmanager
    def hold(self):
        if not self._lock.acquire(blocking=False):
            raise NetworkBusy("ANALYSIS_BUSY")
        try:
            yield self
        finally:
            # Only the executing thread releases; HTTP cancellation has no hook here.
            self._lock.release()

    def _state(self) -> str:
        network = self._network()
        if network is None:
            raise ModelIdentityUnavailable("MODEL_IDENTITY_UNAVAILABLE")
        xml = network.save_to_string("XIIDM", parameters=dict(EXPORT_PARAMETERS))
        root = ET.fromstring(xml)
        if root.tag != "{http://www.powsybl.org/schema/iidm/1_17}network":
            raise ValueError("Unexpected model serialization")
        return _digest({
            "xml": ET.canonicalize(xml, strip_text=True),
            "working_variant": network.get_working_variant_id(),
            "per_unit": network.per_unit,
            "nominal_apparent_power": network.nominal_apparent_power,
        })

    def identify(self, provenance: dict, loadflow_parameters) -> None:
        """Called once by the byte-bound loader, before publishing the object.

        A failed identification leaves legacy calculations usable but all
        revision reads closed until a new load. No source path/mtime is trusted.
        """
        with self.hold():
            if self._generation is not None:
                raise ValueError("Identity is installed only at load time")
            self._generation = str(uuid.uuid4())
            try:
                hashes = [provenance["source_sha256"],
                          *provenance["conversion_code"].values()]
                if (not provenance["conversion_code"]
                        or not all(isinstance(h, str) and re.fullmatch(r"[0-9a-f]{64}", h)
                                   for h in hashes)
                        or provenance["loader"] not in ("kpg-full-matpower-v1", "pypowsybl-import-v1")
                        or not isinstance(provenance["import_parameters"], dict)):
                    raise ValueError("Invalid load provenance")
                lf = copy.deepcopy(loadflow_parameters)
                self._parameters = {
                    "loadflow": lf,
                    "security": pp.security.Parameters(load_flow_parameters=lf),
                    "sensitivity": pp.sensitivity.Parameters(),
                }
                self._providers = {
                    "loadflow": pp.loadflow.get_default_provider(),
                    "security": pp.security.get_default_provider(),
                    "sensitivity": pp.sensitivity.get_default_provider(),
                }
                # Pinned SDK adapter: fail closed if this build identity is absent.
                build = pp._pypowsybl.get_version_table()
                if not isinstance(build, str) or not build.strip():
                    raise ValueError("Engine build identity is missing")
                self._provenance = {**copy.deepcopy(provenance),
                                    "pypowsybl": pp.__version__, "engine_build": build}
                self._initial_state = self._state()
                _digest(self._parameters)  # Verify complete settings are serializable.
            except Exception:
                self._provenance = None
                self._initial_state = None

    def invalidate_identity(self) -> None:
        """Keep legacy execution usable, but never attribute unbound settings."""
        with self.hold():
            self._initial_state = None

    def parameters(self, operation: str):
        """A detached copy of the exact settings the identified model executes."""
        with self.hold():
            if self._parameters is None:
                return None
            return copy.deepcopy(self._parameters[operation])

    def provider(self, operation: str) -> str:
        with self.hold():
            return self._providers[operation] if self._providers is not None else ""

    def capture_revision(self) -> ModelRevision:
        with self.hold():
            if self._provenance is None or self._initial_state is None:
                raise ModelIdentityUnavailable("MODEL_IDENTITY_UNAVAILABLE")
            try:
                state = self._state()
                settings = _digest({"parameters": self._parameters,
                                    "providers": self._providers})
                revision = _digest({"schema": "kpg-working-state-v1",
                                    "generation": self._generation,
                                    "provenance": self._provenance,
                                    "state": state, "settings": settings,
                                    "export": EXPORT_PARAMETERS})
                return ModelRevision(revision, self._generation, state, settings,
                                     state == self._initial_state)
            except Exception as exc:
                raise ModelIdentityUnavailable("MODEL_IDENTITY_UNAVAILABLE") from exc

    def require_revision(self, expected: str) -> ModelRevision:
        current = self.capture_revision()
        if current.revision != expected:
            raise ModelRevisionChanged("MODEL_REVISION_CHANGED")
        return current


def access_for(network) -> NetworkAccess:
    """Exactly one owner per real Python/native Network object, across factories."""
    if not isinstance(network, pp.network.Network):
        raise TypeError("A native Network is required")
    with _creation_lock:
        owner = getattr(network, "_ai_ems_access", None)
        if owner is None:
            owner = NetworkAccess(network)
            network._ai_ems_access = owner
        elif not isinstance(owner, NetworkAccess) or owner._network() is not network:
            raise ModelIdentityUnavailable("MODEL_IDENTITY_UNAVAILABLE")
        return owner


def network_access(network):
    # Existing unit tests use non-native sentinels with mocked calculations.
    # Real native objects, including unprovenanced/private ones, always lock.
    return access_for(network).hold() if isinstance(network, pp.network.Network) else nullcontext()


def guarded(resolve_network):
    """Guard a whole synchronous REST/Tool transaction in its executing thread."""
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            with network_access(resolve_network()):
                return function(*args, **kwargs)
        return wrapped
    return decorate


def guarded_network(function):
    """Guard library functions whose first argument is ``network``."""
    @wraps(function)
    def wrapped(*args, **kwargs):
        network = args[0] if args else kwargs["network"]
        with network_access(network):
            return function(*args, **kwargs)
    return wrapped


def identified_parameters(network, operation: str):
    if not isinstance(network, pp.network.Network):
        return None
    return access_for(network).parameters(operation)


def identified_provider(network, operation: str) -> str:
    if not isinstance(network, pp.network.Network):
        return ""
    return access_for(network).provider(operation)
