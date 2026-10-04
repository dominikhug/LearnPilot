from learnpilot.graph import NodeState, break_cycles, levels, node_states


def test_break_cycles_drops_weakest_edge_of_each_cycle():
    edges = {
        (1, 2): 0.9,
        (2, 3): 0.4,
        (3, 1): 0.8,  # cycle 1 → 2 → 3 → 1, weakest is (2, 3)
        (4, 4): 0.5,  # self-loop
        (3, 4): 0.7,
    }
    dropped = break_cycles([1, 2, 3, 4], edges)
    assert sorted(dropped) == [(2, 3), (4, 4)]
    assert edges == {(1, 2): 0.9, (3, 1): 0.8, (3, 4): 0.7}


def test_break_cycles_handles_overlapping_cycles():
    edges = {(1, 2): 0.9, (2, 1): 0.1, (2, 3): 0.9, (3, 1): 0.2}
    break_cycles([1, 2, 3], edges)
    assert edges == {(1, 2): 0.9, (2, 3): 0.9}


def test_break_cycles_leaves_dag_unchanged():
    edges = {(1, 2): 0.5, (1, 3): 0.5, (2, 4): 0.5, (3, 4): 0.5}
    assert break_cycles([1, 2, 3, 4], edges) == []
    assert len(edges) == 4


def test_levels_follow_longest_prerequisite_chain():
    edges = [(1, 2), (2, 3), (1, 3), (4, 3)]
    assert levels([1, 2, 3, 4, 5], edges) == {1: 1, 2: 2, 3: 3, 4: 1, 5: 1}


def test_node_states_unlock_when_all_prerequisites_mastered():
    edges = [(1, 3), (2, 3), (3, 4)]
    assert node_states([1, 2, 3, 4], edges) == {
        1: NodeState.unlocked,
        2: NodeState.unlocked,
        3: NodeState.locked,
        4: NodeState.locked,
    }
    states = node_states([1, 2, 3, 4], edges, mastered={1, 2}, in_progress={4})
    assert states == {
        1: NodeState.mastered,
        2: NodeState.mastered,
        3: NodeState.unlocked,
        4: NodeState.in_progress,  # started early
    }
