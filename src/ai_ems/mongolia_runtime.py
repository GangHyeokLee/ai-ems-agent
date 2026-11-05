"""One verified Mongolia bundle per process; legacy mode stays opt-in compatible.

Only runtime/static pipeline modules are imported. No Paths/source checkout,
PyPSA, model construction, installation, or activation is involved.
"""
from __future__ import annotations

from dataclasses import dataclass
import importlib.metadata
from pathlib import Path
import sys
import threading

from ai_ems.mongolia_security import ModelFiles, MongoliaModelUnavailable, MongoliaSecurityService
from pipelines.mn.bundle import read_json, require, validate_bundle
from pipelines.mn.bundle_probe import observe
from pipelines.mn.inventory import Checks, compare_numerical


@dataclass(frozen=True)
class BundleFiles:
    map: Path
    pf_preview: Path
    reference: Path
    xiidm: Path
    reports: Path

    def model_files(self):
        return ModelFiles(self.xiidm, self.reports, self.pf_preview, self.map)


class MongoliaBundleRuntime:
    """Fail closed after any integrity failure, until a new process is started."""

    def __init__(self, directory, manifest_sha256):
        self.selected = directory is not None or manifest_sha256 is not None
        self._directory_setting = directory
        self._trusted_sha256 = manifest_sha256
        self._root = None
        self._files = None
        self._manifest = None
        self._service = None
        self._state = "NOT_STARTED" if self.selected else "LEGACY"
        self._error = None
        self._environment = None
        self._numeric_checks = 0
        self._controls = None
        self._sensitivity_definition = None
        self._lock = threading.RLock()

    def _unavailable(self, exc):
        self._state = "UNAVAILABLE"
        self._error = f"{type(exc).__name__}: {exc}"
        self._service = None

    @staticmethod
    def _runtime_environment():
        packages = {name: importlib.metadata.version(name) for name in
                    ("pypowsybl", "numpy", "pandas", "networkx", "scipy", "python-dotenv")}
        require(sys.version_info >= (3, 11), "Python 3.11 or later is required")
        require(packages["pypowsybl"] == "1.16.1", "Unreviewed PyPowSyBl version")
        return {"python": sys.version.split()[0], "packages": packages}

    def start(self):
        with self._lock:
            if self._state != "NOT_STARTED":
                return
            try:
                require(bool(self._directory_setting) and bool(self._trusted_sha256),
                        "Both AI_EMS_MN_BUNDLE_DIR and operator-approved AI_EMS_MN_BUNDLE_MANIFEST_SHA256 are required")
                root = Path(self._directory_setting).expanduser().absolute()
                self._root = root  # Keep lexical spelling; do not resolve away a symlink.
                validate_bundle(root, expected_manifest_sha256=self._trusted_sha256)
                manifest = read_json(root/"manifest.json")
                selected = manifest["runtime_paths"]
                # validate_bundle pins the role/path contract and exact file set.
                files = BundleFiles(**{role: root/selected[role] for role in
                                       ("map", "pf_preview", "reference", "xiidm", "reports")})
                self._environment = self._runtime_environment()
                actual, _ = observe(root)
                checks = Checks()
                compare_numerical(actual, read_json(root/"validation/runtime_contract.json")["numerical_reference"], checks)
                require(len(checks.items) == 186 and checks.ready(), "Live PF/N-1/Sensitivity baseline validation failed")
                service = MongoliaSecurityService(files.model_files())
                status = service.status()
                require(status["available"] and status["sensitivity_available"], "Mongolia runtime model is unavailable")
                validate_bundle(root, expected_manifest_sha256=self._trusted_sha256)
                self._files, self._manifest, self._service = files, manifest, service
                self._numeric_checks = len(checks.items)
                self._controls = actual["loadflow_parameters"]
                self._sensitivity_definition = actual["sensitivity_definition"]
                self._state = "VERIFIED"
            except Exception as exc:
                self._unavailable(exc)

    def guard(self):
        self.start()
        with self._lock:
            if self._state == "VERIFIED":
                try:
                    validate_bundle(self._root, expected_manifest_sha256=self._trusted_sha256)
                except Exception as exc:
                    self._unavailable(exc)
            if self._state != "VERIFIED":
                raise MongoliaModelUnavailable(f"Explicit Mongolia bundle unavailable; no legacy fallback. {self._error}")

    def files(self):
        self.guard()
        return self._files

    def catalog_paths(self):
        try:
            files = self.files()
            return files.map, files.pf_preview
        except MongoliaModelUnavailable:
            return None, None

    def status(self):
        try:
            self.guard()
        except MongoliaModelUnavailable as exc:
            return {"available": False, "reason": str(exc), "sensitivity_available": False,
                    "sensitivity_reason": str(exc)}
        return self._service.status()

    def _run(self, operation, *args, **kwargs):
        self.guard()
        service = self._service
        try:
            return getattr(service, operation)(*args, **kwargs)
        finally:
            # Never return successful results after concurrent bundle mutation.
            self.guard()

    def run_line_contingency(self, *args, **kwargs):
        return self._run("run_line_contingency", *args, **kwargs)

    def run_line_sensitivity(self, *args, **kwargs):
        return self._run("run_line_sensitivity", *args, **kwargs)

    def diagnostics(self):
        if self.selected:
            try:
                self.guard()
            except MongoliaModelUnavailable:
                pass
        paths = None
        if self._files is not None:
            model = self._files.model_files()
            paths = {role: str(getattr(self._files, role)) for role in BundleFiles.__dataclass_fields__}
            paths.update({role: str(getattr(model, role)) for role in
                          ("mapping", "generator_reference", "conversion_validation", "input_hashes")})
        return {"mode": "bundle" if self.selected else "legacy", "validation_status": self._state,
                "available": self._state == "VERIFIED", "process_start_selection": True,
                "bundle_id": self._manifest["bundle_id"] if self._manifest else None,
                "manifest_sha256": self._trusted_sha256, "bundle_directory": str(self._root) if self._root else None,
                "scenario_id": self._manifest["scenario_id"] if self._manifest else None,
                "paths": paths, "numerical_checks": self._numeric_checks,
                "loadflow_parameters": self._controls, "sensitivity_definition": self._sensitivity_definition,
                "environment": self._environment, "error": self._error,
                "legacy_fallback_allowed": not self.selected, "installed_or_activated": False}
