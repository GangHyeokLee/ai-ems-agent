"""Build-only fixtures. Production helpers never load the original scripts."""
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

from pipelines.mn import artifacts
from pipelines.mn.inventory import sha256
from pipelines.mn.paths import resolve


@pytest.fixture
def sample_paths(tmp_path, monkeypatch):
    ems, source, earth = (tmp_path / name for name in ("ems", "source", "independent-earth"))
    for path in (ems, source, earth):
        path.mkdir()
    paths = resolve(SimpleNamespace(ems_root=ems, source_project=source, earth_checkout=earth), {})
    specs = []
    for role, area in (("legacy_model", "MN"), ("map_model", "MN_35k")):
        path = earth / "networks" / area / "base.nc"
        path.parent.mkdir(parents=True)
        path.write_bytes(area.encode())
        specs.append(dict(role=role, path=str(path.relative_to(earth)), sha256=sha256(path)))
    monkeypatch.setattr(artifacts, "load_contract", lambda: {"files": specs})
    return paths


@pytest.fixture(scope="module")
def source_paths():
    if not os.environ.get("KEMS_MN_SOURCE_PROJECT"):
        pytest.skip("Actual-data comparison requires explicit KEMS_MN_SOURCE_PROJECT")
    paths = resolve(SimpleNamespace())
    if not paths.source_project.is_dir() or not paths.earth_checkout.is_dir():
        pytest.skip("Explicit Mongolia source/Earth inputs unavailable")
    return paths


@pytest.fixture(scope="module")
def original_modules(source_paths):
    """Load precisely four pinned reference modules, without sys.path injection."""
    from pipelines.mn.inventory import load_contract
    specs = {s["path"]: s["sha256"] for s in load_contract()["files"] if s["root"] == "source"}
    modules = {}
    with pytest.MonkeyPatch.context() as patch:
        for name in ("audit_mn35k_parameters", "mn35k_lengths", "mn35k_refinement", "mn35k_powsybl"):
            path = source_paths.source_project / "scripts" / (name + ".py")
            assert sha256(path) == specs["scripts/" + name + ".py"], "Reference source differs from Stage 1"
            identifier = "stage2a_reference_" + name
            spec = importlib.util.spec_from_file_location(identifier, path)
            module = importlib.util.module_from_spec(spec)
            patch.setitem(sys.modules, identifier, module)
            if name == "mn35k_lengths":
                patch.setitem(sys.modules, "audit_mn35k_parameters", modules["audit_mn35k_parameters"])
            spec.loader.exec_module(module)
            modules[name] = module
        yield SimpleNamespace(**modules)
