"""Connected components of the undirected bus/line graph and descriptive statistics.

The graph is exactly the input: one node per bus ID, one edge per line between its two bus IDs.
Nothing is merged by distance, crossing or voltage.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Iterable

from .sources import Graph

SIZE_BINS = ((1, 1), (2, 2), (3, 3), (4, 5), (6, 10), (11, 50), (51, 100), (101, None))


class UnionFind:
    def __init__(self, items: Iterable[str]):
        self.parent = {item: item for item in items}

    def find(self, item: str) -> str:
        root = item
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[item] != root:
            self.parent[item], item = root, self.parent[item]
        return root

    def union(self, a: str, b: str) -> bool:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        if rb < ra:
            ra, rb = rb, ra
        self.parent[rb] = ra
        return True

    def groups(self) -> list[list[str]]:
        groups = defaultdict(list)
        for item in self.parent:
            groups[self.find(item)].append(item)
        return [sorted(members) for members in groups.values()]


def components(graph: Graph) -> list[list[str]]:
    """Components ordered like the source build: size descending, then smallest bus ID."""
    uf = UnionFind(graph.buses)
    for line in graph.lines:
        uf.union(line["bus1_id"], line["bus2_id"])
    return sorted(uf.groups(), key=lambda c: (-len(c), c[0]))


def size_bin(size: int) -> str:
    for low, high in SIZE_BINS:
        if size >= low and (high is None or size <= high):
            return f"{low}" if low == high else (f"{low}+" if high is None else f"{low}-{high}")
    raise AssertionError(size)


def analyze(graph: Graph) -> dict[str, Any]:
    comps = components(graph)
    bus_component = {bus: index for index, comp in enumerate(comps) for bus in comp}
    degree = Counter()
    lines_in = Counter()
    length_in = defaultdict(float)
    self_loops, pair_lines = [], defaultdict(list)
    for line in graph.lines:
        a, b = line["bus1_id"], line["bus2_id"]
        if a == b:
            self_loops.append(line["line_id"])
        degree[a] += 1
        degree[b] += 1
        cid = bus_component[a]
        lines_in[cid] += 1
        length_in[cid] += float(line.get("length_km") or 0.0)
        pair_lines[tuple(sorted((a, b)))].append(line["line_id"])
    parallel = [sorted(ids) for ids in pair_lines.values() if len(ids) > 1]
    rows = []
    for cid, comp in enumerate(comps):
        voltages = sorted({graph.buses[b]["voltage_kv"] for b in comp})
        rows.append(dict(component_id=cid, buses=len(comp), lines=lines_in[cid],
                         length_km=round(length_in[cid], 3), voltage_kv=voltages,
                         dangling_buses=sum(degree[b] == 1 for b in comp), size_bin=size_bin(len(comp))))
    by_voltage = {}
    for kv in sorted({b["voltage_kv"] for b in graph.buses.values()}):
        members = [r for r in rows if r["voltage_kv"] == [kv]]
        mixed = [r for r in rows if kv in r["voltage_kv"] and len(r["voltage_kv"]) > 1]
        largest = max(members, key=lambda r: (r["buses"], -r["component_id"]), default=None)
        by_voltage[str(int(kv)) if float(kv).is_integer() else str(kv)] = dict(
            buses=sum(b["voltage_kv"] == kv for b in graph.buses.values()),
            lines=sum(l["voltage_kv"] == kv for l in graph.lines),
            components=len(members), mixed_voltage_components=len(mixed),
            largest_component_id=largest and largest["component_id"],
            largest_component_buses=largest and largest["buses"], largest_component_lines=largest and largest["lines"],
            largest_component_share_of_voltage_buses=largest and round(
                largest["buses"] / max(1, sum(b["voltage_kv"] == kv for b in graph.buses.values())), 4),
            one_line_components=sum(r["lines"] == 1 for r in members))
    largest = rows[0] if rows else None
    stats = dict(
        buses=len(graph.buses), lines=len(graph.lines), connected_components=len(comps),
        largest_component=largest and dict(component_id=0, buses=largest["buses"], lines=largest["lines"],
                                           voltage_kv=largest["voltage_kv"], length_km=largest["length_km"]),
        largest_component_bus_share=largest and round(largest["buses"] / max(1, len(graph.buses)), 4),
        isolated_single_bus_components=sum(r["buses"] == 1 for r in rows),
        one_line_components=sum(r["lines"] == 1 for r in rows),
        size_distribution={label: sum(r["size_bin"] == label for r in rows) for label in
                           [size_bin(low) for low, _ in SIZE_BINS]},
        mixed_voltage_components=sum(len(r["voltage_kv"]) > 1 for r in rows),
        self_loops=len(self_loops), self_loop_line_ids=self_loops,
        parallel_line_groups=len(parallel), parallel_line_ids=parallel,
        dangling_buses=sum(degree[b] == 1 for b in graph.buses), zero_degree_buses=sum(degree[b] == 0 for b in graph.buses),
        degree_distribution={str(k): v for k, v in sorted(Counter(degree[b] for b in graph.buses).items())},
        by_voltage_kv=by_voltage)
    return dict(stats=stats, components=comps, rows=rows, bus_component=bus_component, degree=degree)


def compare_partitions(reference: Graph, ref_components: list[list[str]], other: Graph,
                       other_components: list[list[str]]) -> dict[str, Any]:
    """Same bus/line ID sets and identical component membership (labels may differ)."""
    ref_lines = {l["line_id"]: (l["bus1_id"], l["bus2_id"]) for l in reference.lines}
    other_lines = {l["line_id"]: (l["bus1_id"], l["bus2_id"]) for l in other.lines}
    canonical = lambda comps: sorted(tuple(c) for c in comps)  # noqa: E731
    return dict(source=other.source, buses=len(other.buses), lines=len(other.lines),
                connected_components=len(other_components),
                same_bus_ids=set(reference.buses) == set(other.buses),
                same_line_ids=set(ref_lines) == set(other_lines),
                same_line_endpoints=ref_lines == other_lines,
                same_component_membership=canonical(ref_components) == canonical(other_components))


def stored_component_ids_match(graph: Graph, comps: list[list[str]]) -> bool | None:
    """Whether ``component_id`` stored in the input reproduces the computed partition."""
    stored = {b: graph.buses[b].get("component_id") for b in graph.buses}
    if any(v is None for v in stored.values()):
        return None
    groups = defaultdict(list)
    for bus, cid in stored.items():
        groups[cid].append(bus)
    return sorted(tuple(sorted(g)) for g in groups.values()) == sorted(tuple(c) for c in comps)
