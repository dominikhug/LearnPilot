import anthropic
import httpx2
import pytest
from pdfs import make_pdf
from sqlmodel import Session, func, select

from learnpilot import processing
from learnpilot.config import settings
from learnpilot.models import Chunk, Document, DocumentStatus, ProcessingStep, User

TEXT = "Recursion is when a function calls itself. It needs a base case.\n\n" * 3


def upload(client, name="notes.txt", data: bytes | None = None):
    return client.post("/api/documents", files={"file": (name, data or TEXT.encode())})


def chunk_count(engine) -> int:
    with Session(engine) as session:
        return session.exec(select(func.count()).select_from(Chunk)).one()


def api_error(error_class: type[anthropic.APIStatusError], status: int):
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages/count_tokens")
    return error_class("error", response=httpx2.Response(status, request=request), body=None)


def test_documents_require_login(client):
    assert client.get("/api/documents").status_code == 401
    assert upload(client).status_code == 401


def test_upload_processes_in_background_and_lists(logged_in, token_counter):
    response = upload(logged_in, "Recursion basics.txt")
    assert response.status_code == 201
    created = response.json()
    assert created["title"] == "Recursion basics"
    assert created["status"] == "processing"

    # TestClient runs background tasks before returning, so processing is done.
    document = logged_in.get(f"/api/documents/{created['id']}").json()
    assert document["status"] == "ready"
    assert document["step"] == "done"
    assert document["token_count"] == 1234
    assert token_counter.calls == [TEXT.strip()]

    assert [d["id"] for d in logged_in.get("/api/documents").json()] == [created["id"]]


def test_pdf_chunks_have_page_numbers(logged_in):
    long = "Stacks grow with every call and shrink when a call returns. " * 2
    pdf = make_pdf([f"Page one. {long}", f"Page two. {long}"])
    document = upload(logged_in, "stack.pdf", pdf).json()
    chunks = logged_in.get(f"/api/documents/{document['id']}/chunks").json()
    assert [(c["position"], c["page"]) for c in chunks] == [(0, 1), (1, 2)]


def test_scanned_pdf_is_rejected_and_not_stored(logged_in, engine):
    response = upload(logged_in, "scan.pdf", make_pdf(["", ""]))
    assert response.status_code == 422
    assert "scanned" in response.json()["detail"]
    assert logged_in.get("/api/documents").json() == []
    assert chunk_count(engine) == 0


def test_file_over_upload_limit_is_rejected(logged_in, monkeypatch):
    monkeypatch.setattr(settings, "max_upload_mb", 1)
    response = upload(logged_in, data=b"x" * (1024 * 1024 + 1))
    assert response.status_code == 413
    assert "1 MB" in response.json()["detail"]


def test_far_too_long_text_is_rejected_without_counting(logged_in, token_counter, monkeypatch):
    monkeypatch.setattr(settings, "max_document_tokens", 10)
    response = upload(logged_in, data=b"word " * 30)
    assert response.status_code == 422
    assert token_counter.calls == []


def test_document_over_token_limit_fails_without_retry(logged_in, token_counter, monkeypatch):
    monkeypatch.setattr(settings, "max_document_tokens", 1000)
    token_counter.result = 1001
    document_id = upload(logged_in).json()["id"]

    document = logged_in.get(f"/api/documents/{document_id}").json()
    assert document["status"] == "failed"
    assert document["token_count"] == 1001
    assert "1,001 tokens; the limit is 1,000" in document["error_message"]
    assert document["can_retry"] is False
    assert logged_in.post(f"/api/documents/{document_id}/retry").status_code == 409


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (api_error(anthropic.OverloadedError, 529), "not reachable"),
        (api_error(anthropic.AuthenticationError, 401), "rejected the API key"),
        (processing.llm.LlmNotConfiguredError(), "not configured"),
        (RuntimeError("boom"), "unexpectedly"),
    ],
)
def test_failed_processing_can_be_retried(logged_in, token_counter, error, message):
    token_counter.result = error
    document_id = upload(logged_in).json()["id"]

    failed = logged_in.get(f"/api/documents/{document_id}").json()
    assert failed["status"] == "failed"
    assert message in failed["error_message"]
    assert failed["can_retry"] is True

    token_counter.result = 500
    assert logged_in.post(f"/api/documents/{document_id}/retry").status_code == 200
    ready = logged_in.get(f"/api/documents/{document_id}").json()
    assert ready["status"] == "ready"
    assert ready["error_message"] is None
    assert ready["token_count"] == 500
    # The retry reran on the stored chunks.
    assert token_counter.calls[-1] == token_counter.calls[0]


def test_ready_document_cannot_be_retried(logged_in):
    document_id = upload(logged_in).json()["id"]
    assert logged_in.post(f"/api/documents/{document_id}/retry").status_code == 409


def test_startup_marks_interrupted_documents_failed(engine, logged_in):
    with Session(engine) as session:
        session.add(
            Document(
                user_id=1,
                title="Interrupted",
                status=DocumentStatus.processing,
                step=ProcessingStep.counting_tokens,
            )
        )
        session.commit()

    with logged_in:  # entering the client runs the app's startup
        documents = logged_in.get("/api/documents").json()

    assert documents[0]["status"] == "failed"
    assert documents[0]["error_message"] == processing.INTERRUPTED_MESSAGE
    assert documents[0]["can_retry"] is True


def test_delete_removes_document_and_chunks(logged_in, engine):
    document_id = upload(logged_in).json()["id"]
    assert chunk_count(engine) > 0

    assert logged_in.delete(f"/api/documents/{document_id}").status_code == 204
    assert logged_in.get(f"/api/documents/{document_id}").status_code == 404
    assert chunk_count(engine) == 0


def test_other_users_documents_are_invisible(logged_in, engine):
    with Session(engine) as session:
        session.add(User(id=2, name="other"))
        session.flush()
        session.add(
            Document(
                id=99,
                user_id=2,
                title="Not yours",
                status=DocumentStatus.ready,
                step=ProcessingStep.done,
            )
        )
        session.commit()

    assert logged_in.get("/api/documents").json() == []
    for request in (logged_in.get, logged_in.delete):
        assert request("/api/documents/99").status_code == 404
    assert logged_in.get("/api/documents/99/chunks").status_code == 404
    assert logged_in.post("/api/documents/99/retry").status_code == 404


def test_limits(logged_in):
    assert logged_in.get("/api/documents/limits").json() == {
        "max_upload_mb": settings.max_upload_mb,
        "max_document_tokens": settings.max_document_tokens,
        "file_types": [".markdown", ".md", ".pdf", ".txt"],
    }
