"""Output boundaries use small local files; no PyPSA or external data required."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from pipelines.mn import artifacts
from pipelines.mn.inventory import sha256
from pipelines.mn.paths import resolve


@pytest.fixture
def paths(tmp_path, monkeypatch):
    ems, source, earth = (tmp_path / name for name in ("ems", "source", "external-earth"))
    for root in (ems, source, earth):
        root.mkdir()
    paths = resolve(SimpleNamespace(ems_root=ems, source_project=source, earth_checkout=earth), {})
    specs = []
    for role, area in (("legacy_model", "MN"), ("map_model", "MN_35k")):
        file = earth / "networks" / area / "base.nc"
        file.parent.mkdir(parents=True)
        file.write_bytes(area.encode())
        specs.append(dict(role=role, path=str(file.relative_to(earth)), sha256=sha256(file)))
    monkeypatch.setattr(artifacts, "load_contract", lambda: {"files": specs})
    return paths


def test_new_artifact_is_exclusive_and_leaves_no_staging_files(paths):
    target = paths.output_dir / "test.nc"
    with artifacts.new_artifact(paths, target) as staged:
        assert not target.exists()
        staged.write_bytes(b"new result")
    assert target.read_bytes() == b"new result"
    assert target.stat().st_nlink == 1
    assert sorted(target.parent.iterdir()) == [target]
    with pytest.raises(ValueError, match="overwrite"):
        with artifacts.new_artifact(paths, target):
            pytest.fail("Existing results must never be reopened")
    assert target.read_bytes() == b"new result"


@pytest.mark.parametrize("area", ["source", "earth", "work", "bundle", "ems-code", "outside"])
def test_artifact_must_use_the_generation_area(paths, tmp_path, area):
    base = {"source": paths.source_project, "earth": paths.earth_checkout, "work": paths.work_dir,
            "bundle": paths.bundle_dir, "ems-code": paths.ems_root / "pipelines",
            "outside": tmp_path / "elsewhere"}[area]
    with pytest.raises(ValueError):
        with artifacts.new_artifact(paths, base / "result.nc"):
            pytest.fail("Invalid output must be rejected before generation")


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "parent-symlink", "broken-symlink"])
def test_links_cannot_alias_or_escape_into_sources(paths, kind):
    source = paths.earth_checkout / "networks/MN/base.nc"
    before = source.read_bytes()
    paths.output_dir.mkdir(parents=True)
    target = paths.output_dir / "alias.nc"
    if kind == "hardlink":
        target.hardlink_to(source)
    elif kind == "symlink":
        target.symlink_to(source)
    elif kind == "broken-symlink":
        target.symlink_to(paths.output_dir / "absent.nc")
    else:
        directory = paths.output_dir / "escape"
        directory.symlink_to(source.parent, target_is_directory=True)
        target = directory / "new.nc"
    with pytest.raises(ValueError):
        with artifacts.new_artifact(paths, target):
            pytest.fail("Alias or escape must be rejected")
    assert source.read_bytes() == before
    assert not (source.parent / "new.nc").exists()


def test_hash_pin_cannot_be_omitted_or_overridden(paths):
    source = paths.earth_checkout / "networks/MN/base.nc"
    with pytest.raises(ValueError, match="conflicts"):
        artifacts.protected_model_hashes(paths, {"pypsa-earth/networks/MN/base.nc": "0" * 64})
    source.write_bytes(b"unexpected data")
    with pytest.raises(ValueError, match="hash changed"):
        with artifacts.new_artifact(paths, paths.output_dir / "result.nc", protected_models={}):
            pytest.fail("An empty custom manifest must not disable fixed source pins")
    assert not paths.output_dir.exists()


def test_hashes_are_checked_again_before_publication(paths):
    target = paths.output_dir / "result.nc"
    with pytest.raises(ValueError, match="hash changed"):
        with artifacts.new_artifact(paths, target) as staged:
            staged.write_bytes(b"unpublished")
            (paths.earth_checkout / "networks/MN/base.nc").write_bytes(b"changed by test")
    assert not target.exists()
    assert not list(paths.output_dir.iterdir())


def test_failed_generation_never_publishes_partial_output(paths):
    target = paths.output_dir / "result.nc"
    with pytest.raises(RuntimeError):
        with artifacts.new_artifact(paths, target) as staged:
            staged.write_bytes(b"partial")
            raise RuntimeError("generator failed")
    assert not target.exists()
    assert not list(paths.output_dir.iterdir())


def test_destination_created_during_generation_is_not_replaced(paths):
    target = paths.output_dir / "result.nc"
    with pytest.raises(ValueError, match="overwrite"):
        with artifacts.new_artifact(paths, target) as staged:
            staged.write_bytes(b"ours")
            target.write_bytes(b"other result")
    assert target.read_bytes() == b"other result"
    assert sorted(paths.output_dir.iterdir()) == [target]


def test_runtime_report_directory_remains_protected(paths):
    paths = replace(paths, runtime_reports=paths.output_dir / "active-reports")
    with pytest.raises(ValueError):
        artifacts.artifact_destination(paths, paths.runtime_reports / "result.json")


@pytest.mark.parametrize("logical", ["../outside", "/absolute"])
def test_custom_protected_names_cannot_escape_logical_roots(paths, logical):
    with pytest.raises(ValueError, match="logical relative"):
        artifacts.protected_model_hashes(paths, {logical: "0" * 64})


def test_package_and_cli_do_not_import_build_dependencies():
    import subprocess
    import sys
    code = "import sys; import pipelines.mn; import pipelines.mn.__main__; assert not {'pypsa','geopandas','pypowsybl'} & sys.modules.keys()"
    result = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_directory_bundle_is_visible_only_when_complete(paths):
    target = paths.output_dir / "reports"
    with artifacts.new_artifact_directory(paths, target, expected_files=("a.csv", "b.md")) as staged:
        (staged / "a.csv").write_text("a\n")
        assert not target.exists()
        (staged / "b.md").write_text("b\n")
        assert not target.exists()
    assert sorted(p.name for p in target.iterdir()) == ["a.csv", "b.md"]
    assert sorted(paths.output_dir.iterdir()) == [target]


@pytest.mark.parametrize("kind", ["empty-directory", "directory", "file", "symlink", "broken-symlink"])
def test_existing_report_destination_is_never_replaced_or_removed(paths, kind):
    paths.output_dir.mkdir(parents=True)
    target = paths.output_dir / "reports"
    if kind in ("directory", "empty-directory"):
        target.mkdir()
        if kind == "directory":
            (target / "old.md").write_bytes(b"old report")
    elif kind == "file":
        target.write_bytes(b"old file")
    else:
        referent = paths.output_dir / "referent"
        if kind == "symlink":
            referent.mkdir()
            (referent / "old.md").write_bytes(b"old report")
        target.symlink_to(referent, target_is_directory=True)
    before = target.lstat()
    with pytest.raises(ValueError, match="overwrite"):
        with artifacts.new_artifact_directory(paths, target, expected_files=("a.csv",)):
            pytest.fail("An existing report destination must not be opened")
    assert target.lstat().st_ino == before.st_ino
    if kind == "directory":
        assert (target / "old.md").read_bytes() == b"old report"
    elif kind == "file":
        assert target.read_bytes() == b"old file"
    elif kind == "symlink":
        assert (referent / "old.md").read_bytes() == b"old report"
    assert not list(paths.output_dir.glob(".mn-report-*"))


@pytest.mark.parametrize("problem", ["missing", "extra", "empty"])
def test_invalid_bundle_is_discarded_before_publication(paths, problem):
    target = paths.output_dir / "reports"
    with pytest.raises(ValueError, match="bundle"):
        with artifacts.new_artifact_directory(paths, target, expected_files=("a.csv", "b.md")) as staged:
            (staged / "a.csv").write_text("a\n")
            if problem != "missing":
                (staged / "b.md").write_text("" if problem == "empty" else "b\n")
            if problem == "extra":
                (staged / "unexpected").write_text("extra")
    assert not target.exists()
    assert not list(paths.output_dir.iterdir())


def test_bundle_rechecks_protected_hashes_before_publication(paths):
    target = paths.output_dir / "reports"
    with pytest.raises(ValueError, match="hash changed"):
        with artifacts.new_artifact_directory(paths, target, expected_files=("a.csv",)) as staged:
            (staged / "a.csv").write_text("a\n")
            (paths.earth_checkout / "networks/MN/base.nc").write_bytes(b"changed by test")
    assert not target.exists()
    assert not list(paths.output_dir.iterdir())


def test_atomic_publication_refuses_even_an_existing_empty_directory(tmp_path):
    staged, target = tmp_path / "staged", tmp_path / "reports"
    staged.mkdir()
    (staged / "a.csv").write_bytes(b"new report")
    target.mkdir()
    inode = target.stat().st_ino
    with pytest.raises(OSError):
        artifacts._publish_directory(staged, target)
    assert target.stat().st_ino == inode
    assert not list(target.iterdir())
    assert (staged / "a.csv").read_bytes() == b"new report"
