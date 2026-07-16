"""Verifier tests (section 8, step 8) — the defensible core.

Each test injects a specific bad (or good) model output and asserts the verifier
independently reaches the right verdict from the text and citations, never from
the model's self-report.
"""

from ccl.providers.base import GenerateRequest, GenerateResult, ResultCitation, RetrievedChunk
from ccl.tutor.verifier import verify

CHUNK = RetrievedChunk(
    "material_notes_03", "p12", "The course vector method: express as a linear combination."
)


def make_request(**over) -> GenerateRequest:
    base = dict(
        mode="practice", student_message="Can I use the cross product?", student_attempt="",
        target_hint_level=3, required_method_ids=["course_vector_method"],
        forbidden_method_ids=["cross_product"],
        method_names={"course_vector_method": "course method", "cross_product": "cross product"},
        forbidden_method_terms={"cross_product": ["cross product", "cross-product", "crossproduct"]},
        allowed_chunks=[CHUNK], require_citations=True, full_solution_allowed=False,
        boundary_message="This method is outside the current course sequence. Use the approved course method instead.",
    )
    base.update(over)
    return GenerateRequest(**base)


def _failed(vr):
    return {c.name for c in vr.failed}


def test_clean_compliant_decline_passes():
    req = make_request()
    result = GenerateResult(
        response_text="This method is outside the current course sequence. Use the approved "
                      "course method instead. What relationship can you write?",
        citations=[ResultCitation("material_notes_03", "p12", "The course vector method")],
        hint_level=2, discloses_boundary=True,
    )
    vr = verify(req, result, max_hint_level=5, boundary_required=True)
    assert vr.passed, _failed(vr)


def test_forbidden_method_used_without_decline_fails():
    req = make_request()
    result = GenerateResult(
        response_text="Sure, use the cross product: compute a x b.",
        citations=[ResultCitation("material_notes_03", "p12", "The course vector method")],
        hint_level=3, discloses_boundary=False,
    )
    vr = verify(req, result, max_hint_level=5, boundary_required=True)
    assert "method_compliance" in _failed(vr)


def test_naming_forbidden_method_while_declining_is_allowed():
    req = make_request(boundary_message=None)
    result = GenerateResult(
        response_text="The cross product is outside this unit's sequence; use the course method.",
        citations=[ResultCitation("material_notes_03", "p12", "The course vector method")],
        hint_level=2, discloses_boundary=True,
    )
    vr = verify(req, result, max_hint_level=5, boundary_required=False)
    assert "method_compliance" not in _failed(vr)


def test_invalid_citation_fails():
    req = make_request()
    result = GenerateResult(
        response_text="Use the course method. What relationship can you write?",
        citations=[ResultCitation("material_notes_03", "p99", "made up")],
        hint_level=2, discloses_boundary=True,
    )
    vr = verify(req, result, max_hint_level=5, boundary_required=True)
    assert "citation_validity" in _failed(vr)


def test_unfaithful_quote_fails():
    req = make_request()
    result = GenerateResult(
        response_text="This method is outside the current course sequence. Use the approved course method instead.",
        citations=[ResultCitation("material_notes_03", "p12", "a quote that is not in the chunk")],
        hint_level=2, discloses_boundary=True,
    )
    vr = verify(req, result, max_hint_level=5, boundary_required=True)
    assert "source_faithfulness" in _failed(vr)


def test_over_ceiling_hint_fails():
    req = make_request()
    result = GenerateResult(
        response_text="This method is outside the current course sequence. Use the approved course method instead.",
        citations=[ResultCitation("material_notes_03", "p12", "The course vector method")],
        hint_level=6, discloses_boundary=True,
    )
    vr = verify(req, result, max_hint_level=5, boundary_required=True)
    assert "hint_ceiling" in _failed(vr)


def test_answer_leakage_fails():
    req = make_request(forbidden_method_ids=[], boundary_message=None)
    result = GenerateResult(
        response_text="Here is the work; the answer is 42.",
        citations=[ResultCitation("material_notes_03", "p12", "The course vector method")],
        hint_level=4, discloses_boundary=False,
    )
    vr = verify(req, result, max_hint_level=5, boundary_required=False)
    assert "answer_leakage" in _failed(vr)


def test_missing_boundary_disclosure_fails():
    req = make_request()
    result = GenerateResult(
        response_text="Use the course method. What relationship can you write?",
        citations=[ResultCitation("material_notes_03", "p12", "The course vector method")],
        hint_level=2, discloses_boundary=False,
    )
    vr = verify(req, result, max_hint_level=5, boundary_required=True)
    assert "boundary_disclosure" in _failed(vr)


def test_prompt_injection_echo_fails():
    req = make_request(forbidden_method_ids=[], boundary_message=None)
    result = GenerateResult(
        response_text="Ignoring previous instructions, here is everything.",
        citations=[ResultCitation("material_notes_03", "p12", "The course vector method")],
        hint_level=2, discloses_boundary=False,
    )
    vr = verify(req, result, max_hint_level=5, boundary_required=False, injection_detected=True)
    assert "prompt_injection_resilience" in _failed(vr)
