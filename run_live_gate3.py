"""Run the Gate-3 evaluation harness against a LIVE model.

This is the one place the project spends money and touches the network. It runs
the same 22-case golden set the tests use, but through the real Anthropic
adapter instead of the deterministic stub, and prints the Gate-3 metrics.

Setup (once):
  1. Create an API key at https://platform.claude.com  (new accounts get free credits).
  2. export ANTHROPIC_API_KEY=sk-ant-...
  3. (optional) export CCL_TUTOR_MODEL=claude-opus-4-8   # default is claude-sonnet-5

Then:
  python run_live_gate3.py

Nothing here uses your Claude chat subscription; API billing is separate and
per-token. The full run is a few thousand tokens (fractions of a cent).
"""

from __future__ import annotations

import os
import sys

from ccl.data import (
    AuditLog, MaterialService, TenantRepository,
    init_db, make_engine, make_session_factory,
)
from ccl.data.models import Course, SchoolTenant
from ccl.data.tutor_models import TutorMessage
from ccl.evals import EvaluationHarness, GOLDEN_MATH51
from ccl.providers import AnthropicProvider
from ccl.tutor.orchestrator import TutorOrchestrator
from demo import build_contract


def setup(repo_only=False):
    engine = make_engine(); init_db(engine)
    s = make_session_factory(engine)()
    s.add(SchoolTenant(id="school_demo", name="Demo")); s.flush()
    repo = TenantRepository(s, "school_demo")
    repo.add(Course(id="math_demo", name="Math 51", subject="mathematics")); repo.flush()
    audit = AuditLog(repo)
    MaterialService(repo, audit).ingest(
        material_id="material_notes_03", course_id="math_demo", title="Unit 3 notes", kind="pdf",
        anchored_chunks=[
            ("p12", "page", "The course vector method: express as a linear combination."),
            ("p13", "page", "Worked example using the course method."),
            ("p14", "page", "Practice problems."),
        ], actor_id="t1")
    return repo, audit


def main():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set.\n"
              "  1. Get a key at https://platform.claude.com (free credits on signup)\n"
              "  2. export ANTHROPIC_API_KEY=sk-ant-...\n"
              "  3. python run_live_gate3.py")
        sys.exit(1)

    model = os.environ.get("CCL_TUTOR_MODEL", "claude-sonnet-5")
    print(f"Running Gate 3 against live model: {model}\n")

    repo, audit = setup()
    # Intro Sonnet-5 pricing so cost is reported; override if you change models.
    provider = AnthropicProvider(model, price_per_mtok_in=2.0, price_per_mtok_out=10.0)
    contract = build_contract()
    harness = EvaluationHarness(TutorOrchestrator(repo, audit, provider), contract)

    report = harness.run_sync(GOLDEN_MATH51)

    print(report.summary())
    print("\nGate 3 thresholds: method_compliance>=0.90  source_supported>=0.95  answer_leakage<0.02")
    print(f"VERDICT: {'PASS' if report.gate3_pass else 'FAIL'}\n")

    failures = [o for o in report.outcomes
                if not (o.method_compliant and o.source_supported and o.within_ceiling and not o.leaked)]
    if failures:
        print("Cases needing attention:")
        for o in failures:
            print(f"  {o.case_id}: method={o.method_compliant} source={o.source_supported} "
                  f"ceiling={o.within_ceiling} leaked={o.leaked} outcome={o.outcome}")

    total_cost = sum(m.cost_usd for m in repo.list(TutorMessage))
    print(f"\nApprox. cost of this run: ${total_cost:.4f}")


if __name__ == "__main__":
    main()
