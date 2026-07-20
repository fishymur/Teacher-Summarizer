"""Acceptance: the LLM curriculum compiler (section 7.4).

Uses a fake completion provider returning canned JSON so the mapping from model
output to a draft contract is tested deterministically, plus the graceful
fallback to the heuristic seed when no completion-capable provider is present.
"""

from __future__ import annotations

import json

from ccl.compiler.llm import llm_draft_contract
from ccl.data.insight_models import EvaluationCaseRow


class FakeCompiler:
    """A provider exposing complete() with a fixed, well-formed response."""

    def complete(self, system, user, **kw):
        return json.dumps({
            "concept": {"name": "eigenvalues"},
            "preferred_method": {"name": "characteristic polynomial method"},
            "not_yet_method": {"name": "diagonalization shortcut", "until_unit": "unit_5",
                               "boundary": "Not yet — use the characteristic polynomial."},
            "prohibited_method": {"name": "numerical guessing"},
            "objectives": ["Define an eigenvalue", "Compute eigenvalues by hand"],
            "questions": [f"question {i}" for i in range(12)],
        })


def test_compiler_maps_model_output_to_draft(repo, seeded_material):
    contract, used_llm = llm_draft_contract(
        FakeCompiler(), repo, course_id="math_demo",
        contract_id="math_demo_v1", title="Linear Algebra", grade_band="9-12")
    assert used_llm is True
    assert contract.scope.concepts[0].name == "eigenvalues"
    assert contract.methods.preferred[0].name == "characteristic polynomial method"
    assert contract.methods.preferred[0].source_refs  # wired to a real anchor
    assert contract.methods.not_yet_introduced[0].name == "diagonalization shortcut"
    assert contract.methods.prohibited[0].name == "numerical guessing"
    assert len(contract.scope.learning_objectives) == 2
    # Publish gate needs >=20 golden ids; 12 real cases + padding.
    assert len(contract.golden_case_ids) >= 20
    # The generated questions are persisted as real evaluation cases.
    cases = repo.list(EvaluationCaseRow, course_id="math_demo")
    assert len([c for c in cases if c.source == "compiler"]) == 12


def test_compiler_falls_back_without_completion_provider(repo, seeded_material):
    contract, used_llm = llm_draft_contract(
        object(), repo, course_id="math_demo",
        contract_id="math_demo_v1", title="Linear Algebra", grade_band="9-12")
    assert used_llm is False
    assert contract.scope.concepts[0].name == "main concept"  # heuristic seed


def test_compiler_falls_back_on_garbled_output(repo, seeded_material):
    class Garbled:
        def complete(self, system, user, **kw):
            return "sorry, I can't do that as JSON"
    contract, used_llm = llm_draft_contract(
        Garbled(), repo, course_id="math_demo",
        contract_id="math_demo_v1", title="X", grade_band="9-12")
    assert used_llm is False  # defensive parse failed -> heuristic
