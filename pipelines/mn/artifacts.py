"""Publish new pipeline artifacts without overwriting sources or prior results.

This module uses only the standard library. Electrical calculations stay in the
migrated helpers and their separately selected build/conversion environments.
"""
from __future__ import annotations

from contextlib import contextmanager
import ctypes
import errno
import os
from pathlib import Path
import sys
import tempfile

from .inventory import load_contract, sha256
from .paths import Paths


def protected_model_hashes(paths: Paths, protected_models=None):
    """Verify fixed source hashes plus any additional legacy config entries.

    Legacy logical paths keep their provenance spelling while an independently
    located Earth checkout is resolved through Paths, never through this file.
    A caller cannot remove or weaken the two Stage 1 source-model hash pins.
    """
    expected = {
        (paths.earth_checkout / spec["path"]).resolve(): spec["sha256"]
        for spec in load_contract()["files"]
        if spec["role"] in ("legacy_model", "map_model")
    }
    for logical, digest in (protected_models or {}).items():
        relative = Path(logical)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Protected model names must be logical relative paths")
        if relative.parts and relative.parts[0] == "pypsa-earth":
            source = paths.earth_checkout.joinpath(*relative.parts[1:]).resolve()
        else:
            source = (paths.source_project / relative).resolve()
        if source in expected and expected[source] != digest:
            raise ValueError("Protected source hash conflicts with the Stage 1 contract")
        expected[source] = digest
    actual = {str(source): sha256(source) for source in expected}
    for source, digest in expected.items():
        if actual[str(source)] != digest:
            raise ValueError(f"Protected source hash changed: {source}")
    return actual


def artifact_destination(paths: Paths, destination: Path):
    """Use only the injected generation area; reject existing aliases and files."""
    paths.validate()
    target = paths.safe_destination(Path(destination))
    if target == paths.output_dir.resolve() or not target.is_relative_to(paths.output_dir.resolve()):
        raise ValueError("Artifacts must be inside the dedicated output_dir")
    raw = Path(destination).expanduser()
    raw = raw if raw.is_absolute() else paths.ems_root / raw
    if raw.is_symlink() or target.exists():
        raise ValueError("Refusing to overwrite an existing artifact, including symlink/hardlink aliases")
    return target


@contextmanager
def new_artifact(paths: Paths, destination: Path, *, protected_models=None):
    """Stage a file, recheck input hashes and publish exclusively, never replace.

    Existing hardlinks are rejected just like every existing destination. The
    final hardlink publication also fails if a destination appears after the
    initial check. Temporary files are created on the destination filesystem.
    """
    target = artifact_destination(paths, destination)
    before = protected_model_hashes(paths, protected_models)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".mn-stage-", dir=target.parent)
    os.close(descriptor)
    staged = Path(temporary)
    try:
        yield staged
        after = protected_model_hashes(paths, protected_models)
        if before != after:
            raise ValueError("Protected source models changed while preparing the artifact")
        # Recheck parent symlinks before publication as well as at entry.
        if artifact_destination(paths, destination) != target:
            raise ValueError("Artifact destination changed during preparation")
        os.link(staged, target, follow_symlinks=False)
    finally:
        staged.unlink(missing_ok=True)


def _publish_directory(staged: Path, target: Path):
    """Rename a complete directory atomically, never replacing even an empty one.

    A check followed by POSIX os.rename would replace an empty directory created
    in between. Linux RENAME_NOREPLACE and Windows rename reject that race. Fail
    closed on other platforms/filesystems instead of using a replacing fallback.
    """
    if sys.platform == "win32":
        os.rename(staged, target)
        return
    if not sys.platform.startswith("linux"):
        raise OSError(errno.ENOSYS, "Atomic no-replace directory publication is unavailable")
    rename = getattr(ctypes.CDLL(None, use_errno=True), "renameat2", None)
    if rename is None:
        raise OSError(errno.ENOSYS, "renameat2 is required for no-replace publication")
    rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    rename.restype = ctypes.c_int
    # AT_FDCWD = -100, RENAME_NOREPLACE = 1 (linux/fs.h).
    if rename(-100, os.fsencode(staged), -100, os.fsencode(target), 1):
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(target))


@contextmanager
def new_artifact_directory(paths: Paths, destination: Path, *, expected_files, protected_models=None):
    """Stage and validate the entire bundle before publishing its directory."""
    expected = tuple(expected_files)
    if not expected or len(set(expected)) != len(expected) or any(Path(name).name != name for name in expected):
        raise ValueError("Expected bundle entries must be unique file names")
    target = artifact_destination(paths, destination)
    before = protected_model_hashes(paths, protected_models)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".mn-report-", dir=target.parent) as temporary:
        staged = Path(temporary)
        yield staged
        if {p.name for p in staged.iterdir()} != set(expected):
            raise ValueError("Report bundle does not contain exactly the expected files")
        for name in expected:
            file = staged / name
            if file.is_symlink() or not file.is_file() or file.stat().st_nlink != 1 or not file.stat().st_size:
                raise ValueError(f"Report bundle entry must be a new, nonempty regular file: {name}")
        if before != protected_model_hashes(paths, protected_models):
            raise ValueError("Protected source models changed while preparing the report bundle")
        if artifact_destination(paths, destination) != target:
            raise ValueError("Report destination changed during preparation")
        _publish_directory(staged, target)
