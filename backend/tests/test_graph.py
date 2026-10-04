from datetime import UTC, datetime, timedelta

from learnpilot.graph import (
    NodeState,
    break_cycles,
    choose_next,
    levels,
    missing_prerequisites,
    node_states,
)


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


# Automatic choice

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def choose(edges, mastered=frozenset(), in_progress=frozenset(), mastery=None, last_seen=None):
    nodes = [1, 2, 3, 4, 5]
    states = node_states(nodes, edges, mastered, in_progress)
    return choose_next(nodes, edges, states, mastery or {}, last_seen or {})


def test_automatic_choice_prefers_the_most_recent_concept_in_progress():
    seen = {1: T0, 2: T0 + timedelta(hours=1)}
    assert choose([], in_progress={1, 2}, mastery={1: 0.1, 2: 0.6}, last_seen=seen) == 2


def test_automatic_choice_then_takes_the_lowest_mastery_among_unlocked():
    edges = [(1, 3), (4, 5)]
    # 1 is mastered; 2, 3 and 4 are unlocked; a lower mastery wins over document order.
    assert choose(edges, mastered={1}, mastery={2: 0.3, 3: 0.2, 4: 0.25}) == 3
    # Mastered stays mastered even when its mastery dropped below the others.
    assert choose(edges, mastered={1, 3}, mastery={1: 0.1, 2: 0.3, 4: 0.25}) == 4


def test_automatic_choice_tie_break_is_the_concept_that_unlocks_the_most():
    # 2 alone unlocks 4 and 5; 1 unlocks nothing alone (3 also needs 2).
    edges = [(1, 3), (2, 3), (2, 4), (2, 5)]
    assert choose(edges) == 2
    # Document order breaks a remaining tie.
    assert choose([]) == 1


def test_automatic_choice_never_picks_mastered_or_locked_concepts():
    edges = [(1, 2), (2, 3), (3, 4), (4, 5)]
    assert choose(edges, mastered={1}) == 2
    assert choose(edges, mastered={1, 2, 3, 4, 5}) is None
    # A locked concept started early counts as in progress.
    assert choose(edges, in_progress={4}, last_seen={4: T0}) == 4


def test_missing_prerequisites_descend_to_learnable_concepts():
    # 5 needs 3 and 4; 3 is locked behind 1 and 2 (2 is mastered); 4 is locked behind 1.
    nodes = [1, 2, 3, 4, 5]
    edges = [(1, 3), (2, 3), (3, 5), (4, 5), (1, 4)]
    states = node_states(nodes, edges, mastered={2})
    assert missing_prerequisites(5, nodes, edges, states) == {1}
    assert missing_prerequisites(3, nodes, edges, states) == {1}
    assert missing_prerequisites(1, nodes, edges, states) == set()
    states = node_states(nodes, edges, mastered={1, 2})
    assert missing_prerequisites(5, nodes, edges, states) == {3, 4}
