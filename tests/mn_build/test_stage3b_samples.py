"""Standard-library bundle safety tests; no external models or build imports."""
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from pipelines.mn import artifacts, bundle
from pipelines.mn import bundle_build as build


@pytest.mark.parametrize("name", ["../escape", "/tmp/escape", "a/../../b", "a\\b", "C:/data", "./a", "a//b", "a/", ""])
def test_unsafe_relative_paths(name):
    with pytest.raises(ValueError, match="Unsafe"):
        bundle.relative_name(name)


@pytest.mark.parametrize("name", ["assets/map.json", "models/network.xiidm", "manifest.json"])
def test_safe_relative_paths(name):
    assert bundle.relative_name(name) == name


@pytest.mark.parametrize("entry", ["symlink", "dangling", "hardlink", "empty", "directory"])
def test_tree_rejects_unsafe_entries(tmp_path, entry):
    root = tmp_path/"bundle"
    root.mkdir()
    source = tmp_path/"source"
    source.write_text("existing original bytes")
    path = root/"bad"
    if entry in ("symlink", "dangling"):
        path.symlink_to(source if entry == "symlink" else tmp_path/"missing")
    elif entry == "hardlink":
        os.link(source, path)
    elif entry == "directory":
        path.mkdir()
    else:
        path.touch()
    with pytest.raises(ValueError):
        bundle.tree_files(root)
    assert source.read_text() == "existing original bytes"


def test_tree_rejects_linked_root_and_parent(tmp_path):
    actual = tmp_path/"actual"
    actual.mkdir()
    (actual/"a").write_text("bytes")
    alias = tmp_path/"alias"
    alias.symlink_to(actual, target_is_directory=True)
    with pytest.raises(ValueError, match="Symlink"):
        bundle.tree_files(alias)


@pytest.mark.parametrize("trusted", [None, "", "not-a-hash", "0"*64])
def test_manifest_requires_external_trust(tmp_path, trusted):
    (tmp_path/"manifest.json").write_text('{"schema":"untrusted"}')
    with pytest.raises(ValueError, match="manifest SHA256"):
        bundle.validate_bundle(tmp_path, expected_manifest_sha256=trusted)


def test_duplicate_and_nonfinite_json(tmp_path):
    path = tmp_path/"bad.json"
    for value in ('{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}'):
        path.write_text(value)
        with pytest.raises(ValueError):
            bundle.read_json(path)


@pytest.mark.parametrize("where", ["source", "earth", "work", "output", "bundle-root", "runtime"])
def test_destination_boundaries(sample_paths, where):
    p = sample_paths
    destination = {"source": p.source_project/"new", "earth": p.earth_checkout/"new",
                   "work": p.work_dir/"new", "output": p.output_dir/"new", "bundle-root": p.bundle_dir,
                   "runtime": p.ems_root/"src/new"}[where]
    with pytest.raises(ValueError):
        build.bundle_destination(p, destination)


@pytest.mark.parametrize("kind", ["file", "directory", "symlink", "dangling", "hardlink"])
def test_existing_destination_never_replaced(sample_paths, kind):
    p = sample_paths
    p.bundle_dir.mkdir(parents=True)
    source = p.work_dir/"keep"
    source.parent.mkdir(parents=True)
    source.write_text("original")
    target = p.bundle_dir/"existing"
    if kind == "directory":
        target.mkdir()
    elif kind == "file":
        target.write_text("keep")
    elif kind == "hardlink":
        os.link(source, target)
    else:
        target.symlink_to(source if kind == "symlink" else source.parent/"missing")
    with pytest.raises(ValueError):
        with build.new_bundle_directory(p, target):
            pytest.fail("Existing target accepted")
    assert source.read_text() == "original"
    assert target.exists() or target.is_symlink()


@pytest.mark.parametrize("direction", ["output-in-work", "work-in-output", "bundle-in-work", "output-in-bundle"])
def test_nested_resolved_paths_rejected(sample_paths, direction):
    p = sample_paths
    changed = {"output-in-work": {"output_dir": p.work_dir/"generated"},
               "work-in-output": {"work_dir": p.output_dir/"work"},
               "bundle-in-work": {"bundle_dir": p.work_dir/"bundle"},
               "output-in-bundle": {"output_dir": p.bundle_dir/"output"}}[direction]
    p = replace(p, **changed)
    with pytest.raises(ValueError):
        build.bundle_destination(p, p.bundle_dir/"new")


@pytest.mark.parametrize("fail_at", [3, 5])
def test_partial_bundle_is_never_published(sample_paths, fail_at):
    target = sample_paths.bundle_dir/"new"
    with pytest.raises(RuntimeError, match="injected"):
        with build.new_bundle_directory(sample_paths, target) as stage:
            for i in range(6):
                (stage/str(i)).write_text("partial content")
                if i+1 == fail_at:
                    raise RuntimeError("injected file generation failure")
    assert not target.exists()
    assert not list(target.parent.glob(".mn-bundle-*"))


def test_atomic_publish_collision_preserves_other_directory(sample_paths, monkeypatch):
    target = sample_paths.bundle_dir/"new"
    publish = artifacts._publish_directory
    def race(stage, dest):
        dest.mkdir()
        (dest/"owner").write_text("concurrent creator")
        publish(stage, dest)
    monkeypatch.setattr(artifacts, "_publish_directory", race)
    with pytest.raises(OSError):
        with build.new_bundle_directory(sample_paths, target) as stage:
            (stage/"complete").write_text("new content")
    assert (target/"owner").read_text() == "concurrent creator"
    assert not (target/"complete").exists()
    assert not list(target.parent.glob(".mn-bundle-*"))


def test_new_bundle_directory_and_parent_link_swap(sample_paths):
    p = sample_paths
    target = p.bundle_dir/"new"
    elsewhere = p.output_dir
    elsewhere.mkdir(parents=True)
    with pytest.raises(ValueError, match="Symlink"):
        with build.new_bundle_directory(p, target) as stage:
            (stage/"result").write_text("result")
            # Restore our own temporary parent before TemporaryDirectory cleanup.
            alias = p.bundle_dir/"redirect"
            alias.symlink_to(elsewhere, target_is_directory=True)
            build.bundle_destination(p, alias/"new")
    assert not target.exists()


def test_static_import_has_no_build_or_runtime_dependencies():
    root = Path(__file__).resolve().parents[2]
    code = """import importlib.abc, sys
class Block(importlib.abc.MetaPathFinder):
 def find_spec(self, name, path=None, target=None):
  if name.split('.')[0] in {'pypsa','pypowsybl','pandas','numpy','ai_ems','xarray'}:
   raise AssertionError('forbidden import: '+name)
sys.meta_path.insert(0, Block())
from pipelines.mn.bundle import read_json, CONTRACT
assert read_json(CONTRACT)['grid_id']=='mn-osm-35k'
"""
    done = subprocess.run([sys.executable, "-B", "-c", code], cwd=root, text=True, capture_output=True)
    assert done.returncode == 0, done.stderr
