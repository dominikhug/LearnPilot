import re

from sqlmodel import Session, select
from test_documents import upload
from test_learning import (
    answer,
    concepts,
    count,
    explain,
    grading,
    key_idea_ids,
    next_question,
    one_per_key_idea,
    start,
)

from learnpilot import graph, llm
from learnpilot.models import (
    Answer,
    Explanation,
    KeyIdea,
    LearnerConceptState,
    LearnerKeyIdeaState,
    PrerequisiteEdge,
    Question,
)


def get_graph(client, document_id: int = 1) -> dict:
    return client.get(f"/api/documents/{document_id}/graph").json()


def states(client) -> dict[str, str]:
    return {c["name"]: c["state"] for c in get_graph(client)["concepts"]}


def key_ideas(client, concept_id: int) -> list[dict]:
    return client.get(f"/api/concepts/{concept_id}/key-ideas").json()


def edit(client, key_idea_id: int, text: str):
    return client.patch(f"/api/key-ideas/{key_idea_id}", json={"text": text})


def add_prerequisite(client, concept_id: int, prerequisite_id: int):
    return client.post(
        f"/api/concepts/{concept_id}/prerequisites", json={"prerequisite_id": prerequisite_id}
    )


# Concepts


def test_rename_concept(logged_in):
    function, _ = concepts(logged_in)
    url = f"/api/concepts/{function['id']}"
    assert logged_in.patch(url, json={"name": "  Functions  "}).status_code == 204
    assert get_graph(logged_in)["concepts"][0]["name"] == "Functions"
    assert logged_in.patch(url, json={"name": " "}).status_code == 422


def test_deleting_a_concept_cascades(logged_in, tutor, engine):
    function, recursion = concepts(logged_in)
    tutor.grade = "missing"
    graded = answer(logged_in, start(logged_in, recursion["id"]).json()["question"]["id"]).json()
    explain(logged_in, graded["answer"]["id"])
    start(logged_in, function["id"])

    assert logged_in.delete(f"/api/concepts/{recursion['id']}").status_code == 204
    assert [c["name"] for c in get_graph(logged_in)["concepts"]] == ["Function"]
    with Session(engine) as db:
        assert {k.concept_id for k in db.exec(select(KeyIdea))} == {function["id"]}
        assert {q.concept_id for q in db.exec(select(Question))} == {function["id"]}
        assert {s.concept_id for s in db.exec(select(LearnerConceptState))} == {function["id"]}
    for model in (Answer, Explanation, LearnerKeyIdeaState, PrerequisiteEdge):
        assert count(engine, model) == 0
    assert logged_in.delete(f"/api/concepts/{recursion['id']}").status_code == 404


def test_deleting_a_prerequisite_unlocks_its_dependents(logged_in):
    function, _ = concepts(logged_in)
    assert states(logged_in)["Recursion"] == "locked"
    logged_in.delete(f"/api/concepts/{function['id']}")
    assert states(logged_in) == {"Recursion": "unlocked"}


# Edges


def test_remove_and_add_an_edge(logged_in):
    function, recursion = concepts(logged_in)
    url = f"/api/concepts/{recursion['id']}/prerequisites/{function['id']}"
    assert logged_in.delete(url).status_code == 204
    assert states(logged_in) == {"Function": "unlocked", "Recursion": "unlocked"}
    assert logged_in.delete(url).status_code == 404

    added = add_prerequisite(logged_in, recursion["id"], function["id"])
    assert added.status_code == 201
    assert added.json()["confidence"] == 1.0
    assert states(logged_in)["Recursion"] == "locked"


def test_edges_that_break_the_graph_are_rejected(logged_in):
    function, recursion = concepts(logged_in)
    duplicate = add_prerequisite(logged_in, recursion["id"], function["id"])
    assert duplicate.status_code == 409
    cycle = add_prerequisite(logged_in, function["id"], recursion["id"])
    assert cycle.status_code == 409
    assert cycle.json()["detail"] == (
        "Recursion builds on Function, so it cannot also be its prerequisite."
    )
    assert add_prerequisite(logged_in, function["id"], function["id"]).status_code == 422

    other = upload(logged_in).json()["id"]
    foreign = get_graph(logged_in, other)["concepts"][0]["id"]
    assert add_prerequisite(logged_in, function["id"], foreign).status_code == 404
    assert len(get_graph(logged_in)["edges"]) == 1


def test_new_edge_into_a_started_concept_keeps_its_progress(logged_in):
    function, recursion = concepts(logged_in)
    logged_in.delete(f"/api/concepts/{recursion['id']}/prerequisites/{function['id']}")
    answer(logged_in, start(logged_in, function["id"]).json()["question"]["id"])

    assert add_prerequisite(logged_in, function["id"], recursion["id"]).status_code == 201
    concept = get_graph(logged_in)["concepts"][0]
    assert (concept["state"], concept["mastery"]) == ("in_progress", 0.5)


def test_has_path():
    edges = [(1, 2), (2, 3), (4, 3)]
    assert graph.has_path(1, 3, edges)
    assert not graph.has_path(3, 1, edges)
    assert not graph.has_path(1, 4, edges)


# Key ideas


def test_key_ideas_list_shows_untested_ones_with_status(logged_in, tutor):
    function, _ = concepts(logged_in)
    tutor.plan = one_per_key_idea(0, 1)
    answer(logged_in, start(logged_in, function["id"]).json()["question"]["id"])
    assert [(k["text"], k["status"]) for k in key_ideas(logged_in, function["id"])] == [
        ("Has parameters", "correct"),
        ("Returns a value", "untested"),
    ]


def test_editing_a_key_idea_resets_its_status(logged_in, tutor, engine):
    function, _ = concepts(logged_in)
    first = answer(logged_in, start(logged_in, function["id"]).json()["question"]["id"]).json()
    assert first["session"]["key_ideas_correct"] == 2
    with Session(engine) as db:
        last_seen = db.exec(select(LearnerConceptState.last_seen)).one()

    [old_id] = key_idea_ids(engine, "Has parameters")
    edited = edit(logged_in, old_id, " Takes parameters ").json()
    assert (edited["text"], edited["status"]) == ("Takes parameters", "untested")
    assert [(k["text"], k["status"]) for k in key_ideas(logged_in, function["id"])] == [
        ("Takes parameters", "untested"),
        ("Returns a value", "correct"),
    ]
    with Session(engine) as db:
        # A graph edit does not count as working on the concept.
        assert db.exec(select(LearnerConceptState.last_seen)).one() == last_seen
    session = start(logged_in, function["id"]).json()
    assert (session["key_ideas_correct"], session["mastery"]) == (1, 0.5)
    with Session(engine) as db:
        # The planned question now tests the new key idea.
        planned = db.exec(select(Question).where(Question.state != "answered")).one()
        assert planned.tested_key_idea_ids == [
            edited["id"],
            *key_idea_ids(engine, "Returns a value"),
        ]

    # A dispute of the older answer grades only what still exists.
    logged_in.post(f"/api/answers/{first['answer']['id']}/dispute", json={"reason": "Wrong"})
    graded_ids = re.search(r"Key ideas to grade: ([\d, ]+)", tutor.calls[-1]["user"])[1]
    assert graded_ids == str(key_idea_ids(engine, "Returns a value")[0])
    statuses = {k["text"]: k["status"] for k in key_ideas(logged_in, function["id"])}
    assert statuses["Takes parameters"] == "untested"


def test_editing_without_a_change_keeps_the_status(logged_in):
    function, _ = concepts(logged_in)
    answer(logged_in, start(logged_in, function["id"]).json()["question"]["id"])
    key_idea = key_ideas(logged_in, function["id"])[0]
    assert edit(logged_in, key_idea["id"], key_idea["text"]).json() == key_idea
    assert edit(logged_in, key_idea["id"], "  ").status_code == 422


def test_edited_key_idea_is_used_when_a_failed_grading_is_retried(logged_in, tutor, engine):
    function, _ = concepts(logged_in)
    question = start(logged_in, function["id"]).json()["question"]
    tutor.grade = llm.LlmOutputError("Nope.", llm.Usage("m", 0, 0))
    answer(logged_in, question["id"])

    [old_id] = key_idea_ids(engine, "Has parameters")
    new_id = edit(logged_in, old_id, "Takes parameters").json()["id"]
    tutor.grade = "correct"
    pending = start(logged_in, function["id"]).json()["ungraded_answer"]
    result = logged_in.post(f"/api/answers/{pending['id']}/grade").json()
    assert {k["id"] for k in result["answer"]["key_ideas"]} == {
        new_id,
        *key_idea_ids(engine, "Returns a value"),
    }


def test_deleting_a_key_idea_updates_questions(logged_in, tutor, engine):
    _, recursion = concepts(logged_in)
    tutor.plan = one_per_key_idea(0, 1, 2)
    first = start(logged_in, recursion["id"]).json()["question"]
    assert first["text"] == "Q1"

    [calls_itself] = key_idea_ids(engine, "Calls itself")
    assert logged_in.delete(f"/api/key-ideas/{calls_itself}").status_code == 204
    # The question on screen tested only that key idea, so it is gone.
    session = start(logged_in, recursion["id"]).json()
    assert (session["question"]["text"], session["key_idea_count"]) == ("Q2", 2)
    assert count(engine, Question) == 2
    assert logged_in.delete(f"/api/key-ideas/{calls_itself}").status_code == 404


def test_deleting_a_failed_key_idea_can_master_the_concept(logged_in, tutor, engine):
    function, recursion = concepts(logged_in)
    tutor.grade = lambda ids: grading({ids[0]: "correct", ids[1]: "missing"})
    answer(logged_in, start(logged_in, function["id"]).json()["question"]["id"])
    tutor.grade = lambda ids: grading({ids[0]: "correct", ids[1]: "missing"})
    answer(logged_in, next_question(logged_in, function["id"])["id"])
    assert states(logged_in)["Function"] == "in_progress"

    [returns] = key_idea_ids(engine, "Returns a value")
    logged_in.delete(f"/api/key-ideas/{returns}")
    concept = get_graph(logged_in)["concepts"][0]
    assert (concept["state"], concept["mastery"]) == ("mastered", 0.75)
    assert states(logged_in)["Recursion"] == "unlocked"


def test_the_last_key_idea_cannot_be_deleted(logged_in):
    function, _ = concepts(logged_in)
    first, second = key_ideas(logged_in, function["id"])
    assert logged_in.delete(f"/api/key-ideas/{first['id']}").status_code == 204
    last = logged_in.delete(f"/api/key-ideas/{second['id']}")
    assert last.status_code == 409


# Access


def test_editing_unknown_or_foreign_ids_is_not_found(logged_in):
    concepts(logged_in)
    assert logged_in.patch("/api/concepts/999", json={"name": "X"}).status_code == 404
    assert logged_in.delete("/api/concepts/999").status_code == 404
    assert logged_in.get("/api/concepts/999/key-ideas").status_code == 404
    assert edit(logged_in, 999, "X").status_code == 404
    assert logged_in.delete("/api/key-ideas/999").status_code == 404


def test_editing_requires_login(client):
    assert client.patch("/api/concepts/1", json={"name": "X"}).status_code == 401
    assert client.delete("/api/key-ideas/1").status_code == 401
