"""Graph logic on prerequisite edges. Edges are (prerequisite, dependent) pairs."""

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
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
    prerequisites = _prerequisites(nodes, edges)

    def state(node: N) -> NodeState:
        if node in mastered:
            return NodeState.mastered
        if node in in_progress:
            return NodeState.in_progress
        if prerequisites[node] <= mastered:
            return NodeState.unlocked
        return NodeState.locked

    return {node: state(node) for node in prerequisites}


def _prerequisites[N](nodes: Iterable[N], edges: Iterable[Edge[N]]) -> dict[N, set[N]]:
    result: dict[N, set[N]] = {node: set() for node in nodes}
    for a, b in edges:
        result[b].add(a)
    return result


def choose_next[N](
    nodes: Sequence[N],
    edges: Iterable[Edge[N]],
    states: Mapping[N, NodeState],
    mastery: Mapping[N, float],
    last_seen: Mapping[N, datetime],
    candidates: Iterable[N] | None = None,
) -> N | None:
    """The automatic choice among `candidates` (default: all nodes).

    In progress first, most recently worked on first; then unlocked by lowest
    mastery, ties going to the node that unlocks the most others. Mastered and
    locked nodes are never chosen. `nodes` is in document order, the last tie-break.
    """
    prerequisites = _prerequisites(nodes, edges)
    mastered = {n for n, s in states.items() if s == NodeState.mastered}
    order = {node: i for i, node in enumerate(nodes)}

    def unlocks(node: N) -> int:
        # Locked dependents whose only missing prerequisite is `node`.
        return sum(
            states[d] == NodeState.locked and prerequisites[d] - mastered == {node} for d in nodes
        )

    def key(node: N) -> tuple:
        if states[node] == NodeState.in_progress:
            return (0, -last_seen[node].timestamp(), 0, order[node])
        return (1, mastery.get(node, 0.0), -unlocks(node), order[node])

    eligible = [
        n
        for n in (nodes if candidates is None else candidates)
        if states[n] in (NodeState.in_progress, NodeState.unlocked)
    ]
    return min(eligible, key=key, default=None)


def missing_prerequisites[N](
    node: N, nodes: Iterable[N], edges: Iterable[Edge[N]], states: Mapping[N, NodeState]
) -> set[N]:
    """The unmastered prerequisites of `node` that can be learned now.

    A locked prerequisite is replaced by its own unmastered prerequisites, down to
    ones that are not locked.
    """
    prerequisites = _prerequisites(nodes, edges)
    result: set[N] = set()
    pending = [p for p in prerequisites[node] if states[p] != NodeState.mastered]
    seen = set(pending)
    while pending:
        current = pending.pop()
        if states[current] != NodeState.locked:
            result.add(current)
            continue
        for p in prerequisites[current] - seen:
            if states[p] != NodeState.mastered:
                seen.add(p)
                pending.append(p)
    return result
