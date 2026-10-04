from conftest import sample_extraction
from sqlmodel import Session, func, select
from test_documents import TEXT, upload

from learnpilot import llm
from learnpilot.extraction import Extraction, ExtractionError
from learnpilot.models import Concept, Document, KeyIdea, LlmCall, PrerequisiteEdge


def count(engine, model) -> int:
    with Session(engine) as session:
        return session.exec(select(func.count()).select_from(model)).one()


def llm_calls(engine) -> list[LlmCall]:
    with Session(engine) as session:
        return list(session.exec(select(LlmCall)))


def test_processing_builds_the_concept_graph(logged_in, extractor, engine):
    document = upload(logged_in, "Recursion.txt").json()
    chunk_ids = [c["id"] for c in logged_in.get(f"/api/documents/{document['id']}/chunks").json()]

    ready = logged_in.get(f"/api/documents/{document['id']}").json()
    assert (ready["status"], ready["step"]) == ("ready", "done")
    assert ready["language"] == "en"
    assert ready["concept_count"] == 2
    assert logged_in.get("/api/documents").json()[0]["concept_count"] == 2

    graph = logged_in.get(f"/api/documents/{document['id']}/graph").json()
    function, recursion = graph["concepts"]
    assert function["name"] == "Function"
    assert (function["level"], function["state"], function["prerequisite_ids"]) == (
        1,
        "unlocked",
        [],
    )
    assert (recursion["level"], recursion["state"]) == (2, "locked")
    assert recursion["prerequisite_ids"] == [function["id"]]
    assert recursion["key_idea_count"] == 3
    assert recursion["source_chunk_ids"] == chunk_ids[:1]
    assert "key_ideas" not in recursion  # untested key ideas are the rubric
    assert graph["edges"] == [
        {"from_concept_id": function["id"], "to_concept_id": recursion["id"], "confidence": 0.9}
    ]

    # The whole document went into one request, as delimited chunks.
    assert len(extractor.calls) == 1
    assert f'<chunk id="{chunk_ids[0]}"' in extractor.calls[0]
    assert "base case" in extractor.calls[0]

    [call] = llm_calls(engine)
    assert (call.document_id, call.purpose, call.input_tokens) == (
        document["id"],
        "concept_extraction",
        100,
    )


def test_invalid_references_from_the_model_are_dropped(logged_in, extractor):
    def with_unknown_references(chunk_ids: list[int]) -> Extraction:
        result = sample_extraction(chunk_ids)
        result.concepts[0].source_chunk_ids.append(12345)
        result.prerequisites.append(result.prerequisites[0].model_copy(update={"concept_id": 9}))
        return result

    extractor.result = with_unknown_references
    document_id = upload(logged_in).json()["id"]
    graph = logged_in.get(f"/api/documents/{document_id}/graph").json()
    assert 12345 not in graph["concepts"][0]["source_chunk_ids"]
    assert len(graph["edges"]) == 1


def test_failed_extraction_can_be_retried_without_duplicates(logged_in, extractor, engine):
    extractor.result = llm.LlmOutputError(
        "The AI service declined this request.", llm.Usage("m", 7, 0)
    )
    document_id = upload(logged_in).json()["id"]

    failed = logged_in.get(f"/api/documents/{document_id}").json()
    assert failed["status"] == "failed"
    assert failed["step"] == "extracting_concepts"
    assert failed["error_message"] == (
        "Finding concepts failed. The AI service declined this request."
    )
    assert failed["can_retry"] is True
    assert [c.input_tokens for c in llm_calls(engine)] == [7]  # failed calls cost tokens too

    extractor.result = sample_extraction
    assert logged_in.post(f"/api/documents/{document_id}/retry").status_code == 200
    assert logged_in.get(f"/api/documents/{document_id}").json()["status"] == "ready"
    assert logged_in.post(f"/api/documents/{document_id}/retry").status_code == 409
    assert count(engine, Concept) == 2


def test_document_without_usable_concepts_fails(logged_in, extractor):
    extractor.result = ExtractionError("No concepts could be found in this document.")
    document_id = upload(logged_in).json()["id"]
    failed = logged_in.get(f"/api/documents/{document_id}").json()
    assert failed["status"] == "failed"
    assert "No concepts" in failed["error_message"]


def test_document_deleted_during_extraction_saves_nothing(logged_in, extractor, engine):
    def delete_then_answer(chunk_ids: list[int]) -> Extraction:
        with Session(engine) as session:
            session.delete(session.exec(select(Document)).one())
            session.commit()
        return sample_extraction(chunk_ids)

    extractor.result = delete_then_answer
    upload(logged_in)
    assert count(engine, Concept) == 0
    [call] = llm_calls(engine)
    assert call.document_id is None


def test_delete_removes_concepts_but_keeps_llm_calls(logged_in, engine):
    document_id = upload(logged_in).json()["id"]
    assert count(engine, KeyIdea) == 5
    assert count(engine, PrerequisiteEdge) == 1

    assert logged_in.delete(f"/api/documents/{document_id}").status_code == 204
    for model in (Concept, KeyIdea, PrerequisiteEdge):
        assert count(engine, model) == 0
    [call] = llm_calls(engine)
    assert call.document_id is None


def test_over_token_limit_skips_extraction(logged_in, token_counter, extractor, monkeypatch):
    from learnpilot.config import settings

    monkeypatch.setattr(settings, "max_document_tokens", 1000)
    token_counter.result = 1001
    upload(logged_in, data=TEXT.encode())
    assert extractor.calls == []


def test_graph_of_other_users_document_is_invisible(logged_in):
    assert logged_in.get("/api/documents/99/graph").status_code == 404
