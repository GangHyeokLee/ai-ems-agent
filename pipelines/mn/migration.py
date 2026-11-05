"""AST-only import/path audit: source scripts are never imported or executed."""
from __future__ import annotations

import ast
from graphlib import CycleError, TopologicalSorter

from .inventory import sha256


def inspect_migration(source_project):
    scripts = sorted((source_project / "scripts").glob("*.py"))
    names = {p.stem for p in scripts}
    records, graph = [], {}
    for path in scripts:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split('.')[0])
            elif isinstance(node, ast.Import):
                imported.update(alias.name.split('.')[0] for alias in node.names)
        dependencies = sorted(imported & names)
        graph[path.stem] = set(dependencies)
        couplings = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.Call)):
                expression = ast.unparse(node)
                if any(marker in expression for marker in ("Path(__file__)", "Path.home()", "sys.path.insert", "ROOT /", "EARTH /")):
                    couplings.append({"line": node.lineno, "expression": expression[:500]})
        records.append({"file": path.name, "sha256": sha256(path), "local_imports": dependencies,
                        "other_imports": sorted(imported - names),
                        "path_couplings": sorted(couplings, key=lambda item:(item["line"], item["expression"]))})
    groups = []
    try:
        sorter = TopologicalSorter(graph)
        sorter.prepare()
        while sorter.is_active():
            group = sorted(sorter.get_ready())
            groups.append(group)
            sorter.done(*group)
    except CycleError as exc:
        return {"scripts": records, "dependency_groups": groups, "cycle": list(exc.args[1]), "ready": False}
    return {"scripts": records, "dependency_groups": groups, "ready": True,
            "note": "의존 순서만 분석함. 실행·복사·이관은 수행하지 않음. 먼저 경로 결합을 해소해야 함."}
