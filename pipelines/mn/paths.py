"""Explicit paths and conservative write boundaries for stage-one inspection."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import os
from pathlib import Path


@dataclass(frozen=True)
class Paths:
    ems_root: Path
    source_project: Path
    earth_checkout: Path
    work_dir: Path
    output_dir: Path
    bundle_dir: Path
    runtime_python: Path
    build_python: Path
    conversion_python: Path
    runtime_xiidm: Path | None = None
    runtime_reports: Path | None = None

    def report(self):
        return {name: str(value) if value is not None else None for name, value in asdict(self).items()}

    def safe_destination(self, path: Path):
        """Resolve symlinks too. Stage one can write under EMS outputs or outside source trees."""
        expanded = path.expanduser()
        target = (expanded if expanded.is_absolute() else self.ems_root / expanded).resolve()
        for source in (self.source_project, self.earth_checkout, self.runtime_reports, self.runtime_xiidm):
            if source is None:
                continue
            if target.is_relative_to(source):
                raise ValueError(f"읽기 전용 원본 경로에 쓸 수 없습니다: {target}")
        if target.is_relative_to(self.ems_root) and not target.is_relative_to(self.ems_root / "outputs"):
            raise ValueError(f"EMS 보고서·작업 경로는 outputs/ 아래여야 합니다: {target}")
        return target

    def validate(self):
        for name in ("work_dir", "output_dir", "bundle_dir"):
            path = getattr(self, name)
            self.safe_destination(path)
            if path.exists() and not path.is_dir():
                raise ValueError(f"디렉터리 대신 파일이 지정됐습니다: {name}={path}")
        if self.work_dir.is_relative_to(self.output_dir) or self.output_dir.is_relative_to(self.work_dir):
            raise ValueError("작업 영역과 합성·변환 출력 영역은 겹칠 수 없습니다.")
        for other in (self.work_dir, self.output_dir):
            if self.bundle_dir.is_relative_to(other) or other.is_relative_to(self.bundle_dir):
                raise ValueError("배포 번들은 작업·생성 출력 영역과 겹칠 수 없습니다.")
        return self


def resolve(args, environ=None):
    env = os.environ if environ is None else environ

    def setting(name, default=None):
        value = getattr(args, name, None)
        if value is None:
            value = env.get("KEMS_MN_" + name.upper())
        return value if value not in (None, "") else default

    # Relative settings always use the selected EMS root, independent of shell cwd.
    ems = Path(setting("ems_root", Path(__file__).resolve().parents[2])).expanduser().resolve()

    def path(name, default=None):
        raw = setting(name, default)
        if raw is None:
            return None
        result = Path(raw).expanduser()
        absolute = result if result.is_absolute() else ems / result
        # Resolving a venv's final python symlink would silently select the base environment.
        return absolute.parent.resolve() / absolute.name if name.endswith("_python") else absolute.resolve()

    source = path("source_project")
    if source is None:
        raise ValueError("--source-project 또는 KEMS_MN_SOURCE_PROJECT를 명시하세요.")
    earth = path("earth_checkout", source / "pypsa-earth")
    return Paths(
        ems, source, earth,
        path("work_dir", "outputs/mn/work"),
        path("output_dir", "outputs/mn/generated"),
        path("bundle_dir", "outputs/mn/bundles"),
        path("runtime_python", ems / ".venv/bin/python"),
        path("build_python", earth / ".pixi/envs/default/bin/python"),
        path("conversion_python", source / ".venv-powsybl/bin/python"),
        path("runtime_xiidm"), path("runtime_reports"),
    ).validate()


def active_runtime(paths, configuration):
    """Inspect actual server settings; never set environment variables or switch the runtime."""
    return replace(
        paths,
        runtime_xiidm=paths.runtime_xiidm or Path(configuration["xiidm"]).resolve(),
        runtime_reports=paths.runtime_reports or Path(configuration["reports"]).resolve(),
    ).validate()
