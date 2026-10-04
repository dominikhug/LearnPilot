import pytest

from learnpilot.extraction import Extraction, ExtractionError, build_prompt, validate
from learnpilot.models import Chunk


def concept(id, name, key_ideas=("a", "b"), sources=(1,), definition="d"):
    return {
        "id": id,
        "name": name,
        "definition": definition,
        "key_ideas": list(key_ideas),
        "source_chunk_ids": list(sources),
    }


def extraction(concepts, prerequisites=(), language="en"):
    return Extraction.model_validate(
        {
            "language": language,
            "concepts": concepts,
            "prerequisites": [
                {"prerequisite_id": a, "concept_id": b, "confidence": c}
                for a, b, c in prerequisites
            ],
        }
    )


def test_unknown_chunks_and_concepts_are_dropped():
    draft = validate(
        extraction(
            [concept(1, "Stack", sources=[1, 99, 1]), concept(2, "Recursion", sources=[2])],
            prerequisites=[(1, 2, 0.9), (1, 77, 0.9), (55, 2, 0.9), (2, 2, 0.5)],
        ),
        chunk_ids={1, 2},
    )
    assert [c.source_chunk_ids for c in draft.concepts] == [[1], [2]]
    assert draft.edges == {(0, 1): 0.9}


def test_concepts_without_sources_or_with_too_few_key_ideas_are_dropped():
    draft = validate(
        extraction(
            [
                concept(1, "No sources", sources=[99]),
                concept(2, "One idea", key_ideas=["only", "  "]),
                concept(3, "Kept"),
            ],
            prerequisites=[(1, 3, 0.9), (2, 3, 0.9)],
        ),
        chunk_ids={1},
    )
    assert [c.name for c in draft.concepts] == ["Kept"]
    assert draft.edges == {}


def test_key_ideas_are_capped_at_eight_and_deduplicated():
    ideas = ["same", "same"] + [f"idea {i}" for i in range(10)]
    draft = validate(extraction([concept(1, "Big", key_ideas=ideas)]), chunk_ids={1})
    assert draft.concepts[0].key_ideas == ["same"] + [f"idea {i}" for i in range(7)]


def test_duplicate_names_are_merged():
    draft = validate(
        extraction(
            [
                concept(1, "Recursion", key_ideas=["a", "b"], sources=[1]),
                concept(2, "Base case"),
                concept(3, " recursion ", key_ideas=["b", "c"], sources=[2]),
            ],
            prerequisites=[(2, 3, 0.8)],
        ),
        chunk_ids={1, 2},
    )
    assert [c.name for c in draft.concepts] == ["Recursion", "Base case"]
    assert draft.concepts[0].key_ideas == ["a", "b", "c"]
    assert draft.concepts[0].source_chunk_ids == [1, 2]
    assert draft.edges == {(1, 0): 0.8}


def test_cycles_are_broken_and_confidence_clamped():
    draft = validate(
        extraction(
            [concept(1, "A"), concept(2, "B"), concept(3, "C")],
            prerequisites=[(1, 2, 1.5), (2, 3, 0.9), (3, 1, 0.2)],
        ),
        chunk_ids={1},
    )
    assert draft.edges == {(0, 1): 1.0, (1, 2): 0.9}


@pytest.mark.parametrize(("language", "expected"), [("de", "de"), (" EN ", "en"), ("german", None)])
def test_language_must_be_iso_639_1(language, expected):
    draft = validate(extraction([concept(1, "A")], language=language), chunk_ids={1})
    assert draft.language == expected


def test_nothing_usable_is_an_error():
    with pytest.raises(ExtractionError, match="No concepts"):
        validate(extraction([concept(1, "A", sources=[5])]), chunk_ids={1})


def test_prompt_delimits_chunks_with_ids_and_locations():
    chunks = [
        Chunk(id=7, document_id=1, position=0, page=3, text="Text on page 3"),
        Chunk(id=8, document_id=1, position=1, section='Intro › "Why"', text="Intro text"),
    ]
    prompt = build_prompt("Notes", chunks)
    assert prompt.startswith('<document title="Notes">')
    assert '<chunk id="7" location="page 3">\nText on page 3\n</chunk>' in prompt
    assert '<chunk id="8" location="Intro › &quot;Why&quot;">' in prompt
    assert prompt.endswith("</document>")
