"""Graph logic on prerequisite edges. Edges are (prerequisite, dependent) pairs."""

from collections.abc import Iterable
from enum import StrEnum
from itertools import pairwise

type Edge[N] = tuple[N, N]


def break_cycles[N](nodes: Iterable[N], edges: dict[Edge[N], float]) -> list[Edge[N]]:
    """Makes the graph a DAG by dropping the weakest edge of each cycle, in place.

    Returns the dropped edges.
    """
    dropped: list[Edge[N]] = []
    nodes = list(nodes)
    while cycle := _find_cycle(nodes, edges):
        weakest = min(cycle, key=lambda edge: edges[edge])
        del edges[weakest]
        dropped.append(weakest)
    return dropped


def _find_cycle[N](nodes: list[N], edges: dict[Edge[N], float]) -> list[Edge[N]] | None:
    successors: dict[N, list[N]] = {node: [] for node in nodes}
    for a, b in edges:
        successors[a].append(b)

    visited: set[N] = set()
    for root in nodes:
        if root in visited:
            continue
        # Iterative depth-first search; `path` holds the nodes on the current branch.
        path: list[N] = [root]
        on_path = {root}
        pending = [iter(successors[root])]
        visited.add(root)
        while pending:
            child = next(pending[-1], None)
            if child is None:
                pending.pop()
                on_path.discard(path.pop())
            elif child in on_path:
                loop = path[path.index(child) :] + [child]
                return list(pairwise(loop))
            elif child not in visited:
                visited.add(child)
                path.append(child)
                on_path.add(child)
                pending.append(iter(successors[child]))
    return None


def levels[N](nodes: Iterable[N], edges: Iterable[Edge[N]]) -> dict[N, int]:
    """Level 1 has no prerequisites; level n builds only on lower levels. Needs a DAG."""
    prerequisites: dict[N, list[N]] = {node: [] for node in nodes}
    for a, b in edges:
        prerequisites[b].append(a)

    result: dict[N, int] = {}

    def level(node: N) -> int:
        if node not in result:
            result[node] = 1 + max((level(p) for p in prerequisites[node]), default=0)
        return result[node]

    for node in prerequisites:
        level(node)
    return result


class NodeState(StrEnum):
    locked = "locked"
    unlocked = "unlocked"
    in_progress = "in_progress"
    mastered = "mastered"


def node_states[N](
    nodes: Iterable[N],
    edges: Iterable[Edge[N]],
    mastered: set[N] = frozenset(),
    in_progress: set[N] = frozenset(),
) -> dict[N, NodeState]:
    """Unlocked is derived, never stored: all prerequisites mastered."""
    prerequisites: dict[N, set[N]] = {node: set() for node in nodes}
    for a, b in edges:
        prerequisites[b].add(a)

    def state(node: N) -> NodeState:
        if node in mastered:
            return NodeState.mastered
        if node in in_progress:
            return NodeState.in_progress
        if prerequisites[node] <= mastered:
            return NodeState.unlocked
        return NodeState.locked

    return {node: state(node) for node in prerequisites}
