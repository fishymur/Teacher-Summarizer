# Curriculum Coherence Layer

A model-agnostic curriculum coherence and verification layer for schools. Built
in small, testable milestones from the build-context spec. The 16-week plan
orders the work Discovery → Curriculum Compiler → **Tutor Runtime** → Insights →
Hardening → Pilot.

**Status:** Milestones 1–3, the Gate-4 hardening build (RBAC, retention/deletion, provider registry, prompt-injection battery), and a runnable local **web app** (teacher studio + tutor + insights). 114 acceptance tests pass. Gate 3 passes on a live model.

## Run the app (what a teacher uses)

No extra dependencies — it runs on the standard library.

```bash
source .venv/bin/activate
# optional: use the real model instead of the offline stub
export ANTHROPIC_API_KEY=sk-ant-...
python -m ccl.web            # then open http://localhost:8000
```

The app opens on a seeded example course so the Tutor works immediately. Without
a key it uses the deterministic offline stub; with a key it uses the live model.

## Run the checks and command-line demos

```bash
python -m venv .venv && source .venv/bin/activate
pip install pydantic sqlalchemy pytest
python -m pytest -v          # 114 acceptance tests
python run_live_gate3.py     # Gate-3 harness against a live model (needs the key)
python demo.py               # Milestone 1: contract lifecycle + policy decision
python demo_tutor.py         # Milestone 2: student flow, headless & offline
python demo_insights.py      # Milestone 3: weekly teacher insight brief
python demo_rbac.py          # Hardening: role-based access control
python demo_privacy.py       # Hardening: retention, erasure, export
python demo_provider.py      # Hardening: provider registry + runtime gate
python demo_injection.py     # Hardening: prompt-injection battery
```

---

## Milestone 1 — Curriculum Contract foundation (the executable-pedagogy core)

Everything the tutor depends on, stopping at the tutor boundary.

| Area | Spec | Module |
|---|---|---|
| Multi-tenant data foundation, tenant isolation at the access layer | §11 #1 | `ccl/data/repository.py`, `ccl/data/models.py` |
| Materials → versions → **immutable** source chunks with page anchors | MVP #2 | `ccl/data/services.py` |
| Curriculum Contract schema | §6 | `ccl/contracts/schema.py` |
| Lifecycle state machine + versioning + publish/supersede | §6, §11 | `ccl/contracts/lifecycle.py`, `ccl/data/services.py` |
| Publish-gate validator (all nine rules) | §6 | `ccl/validation/validator.py` |
| Deterministic policy engine → `PolicyDecision` | §14 | `ccl/policy/` |
| Append-only audit log | §11 | `ccl/data/audit.py` |

---

## Milestone 2 — Tutor Runtime (headless, golden-set driven)

Plugs a model in *behind* the policy layer and proves each response obeyed the
contract before it could reach a student. No chat UI yet — that comes only after
the Gate 3 numbers hold.

| Area | Spec | Module |
|---|---|---|
| Provider adapter (model-independent) + real Anthropic adapter | §15 | `ccl/providers/` |
| Approved-source retrieval | §8, §10 | `ccl/tutor/retrieval.py` |
| Request classifier (concept/solution/clarify/off-topic/unsafe/injection) | §8 | `ccl/tutor/classifier.py` |
| Hint planner with attempt gating ("diagnose before explaining") | §7, §8 | `ccl/tutor/planner.py` |
| **Deterministic verifier** (citations, faithfulness, method compliance, leakage, boundary, injection) | §8 | `ccl/tutor/verifier.py` |
| Orchestrator: classify → decide → retrieve → generate → verify → revise-once → fallback → persist | §8 | `ccl/tutor/orchestrator.py` |
| Learn / Practice / Review / Assessment modes | §8 | policy + planner |
| Evaluation harness + 22-case golden set + Gate 3 gates | §16 | `ccl/evals/` |

### The design that makes it defensible

The verifier never trusts the model's self-report. It checks the structured
output *and* the raw text against the policy decision and the approved chunks: a
citation must resolve to a real anchor, a quote must appear verbatim in its
cited chunk, a forbidden method term may appear only inside a sanctioned
boundary decline, and the hint level must sit under the mode ceiling. The model
proposes; the deterministic verifier disposes. If verification fails, the
runtime revises once, then falls back safely and offers teacher escalation — it
never ships a violation.

### Gate 3 result (22 golden cases, offline stub providers)

```
COMPLIANT model  -> method_compliance=1.00 source_supported=1.00 answer_leakage=0.00 answered=0.95 gate3=PASS
UNALIGNED model  -> method_compliance=1.00 source_supported=0.68 answer_leakage=0.00 answered=0.00 gate3=FAIL
```

The gate discriminates. A compliant model clears the thresholds (method ≥0.90,
source ≥0.95, leakage <0.02) and answers 21/22 cases (only the safety case falls
back). An unaligned model is *caught*: nothing leaks to the student, but it
falls back on every turn, so it can never cite the required sources and source
support collapses — the honest "this model cannot meet the contract" signal.

---

## Milestone 3 — Teacher Insights (aggregate, no surveillance)

Turns interaction events into evidence-backed patterns a teacher can act on,
without ever exposing individual students.

| Area | Spec | Module |
|---|---|---|
| Interaction event model (help, attempt, full-solution, high-support, boundary, fallback) | §10 | `ccl/tutor/orchestrator.py` |
| Aggregation with **minimum-cohort suppression** (default 5) | §9.3, §10 | `ccl/insights/aggregate.py` |
| Evidence-backed inference (confidence, sample size, counter-evidence, source refs) | §10 | `ccl/insights/aggregate.py`, `ccl/insights/types.py` |
| Observed / inferred / recommended separation, enforced by the type system | §9.2 | `ccl/insights/types.py` |
| Weekly brief (clusters, prerequisite gaps, full-solution pressure, week-over-week change) | §9.C | `ccl/insights/brief.py` |
| Review loop (confirm / incorrect / not-useful / merge) | §9.C | `ccl/insights/review.py` |
| Correction loop → **draft** contract change or new eval case (never edits published) | §9.4 | `ccl/insights/review.py` |
| Gated, audited raw-transcript access | §9.3, §17 | `ccl/insights/review.py` |
| Signal-quality metrics (actionability, precision, correction rate, privacy exceptions) | §9.5 | `ccl/insights/metrics.py` |

### The privacy posture, enforced not promised

Minimum-cohort suppression is a hard gate in the aggregator: a concept touched
by fewer than five distinct students produces nothing at all. Student ids are
used only to count distinct people and never leave the aggregation layer — the
view types have no field to hold a student id, a ranking, or a "struggling
student" label, and the allowed insight types are a closed set with no
demographic or sensitive-trait category. Raw transcripts are reachable only
through one service that requires a documented reason and a valid justification
and writes an append-only audit event; the brief never touches it. A teacher
correction forks a *draft* contract version — the published contract that
governs students is never silently edited.

---

## Hardening (Gate 4) — slice 1: Role-based access control

The first of the §17 pilot-readiness requirements: least privilege, separation
of duties, and course scoping, enforced at a single boundary and audited.

| Area | Spec | Module |
|---|---|---|
| Roles, permissions, permission matrix | §3, §17 | `ccl/access/roles.py` |
| Capability check + audited denials | §17 | `ccl/access/controller.py` |
| Role grant / revoke / load principal | §11, §17 | `ccl/access/roleservice.py` |
| `Workspace` — the enforced boundary for sensitive operations | §17 | `ccl/access/workspace.py` |

Design decisions a privacy reviewer will ask about: a **student** can use the
tutor and read only their own transcript; a **teacher** authors/approves/
publishes contracts, reviews insights, and reads a student transcript only with
a documented reason — all scoped to their own courses; a **chair** is a teacher
with cross-course reach; an **admin** runs the plumbing (roles, retention,
providers, audit) but has **no** curriculum authority and **no** access to
student learning data. Every refused attempt writes an append-only
`access.denied` event. Authorization lives at the `Workspace` boundary; the domain services beneath it
assume an already-authorized caller.

---

## Hardening (Gate 4) — slice 2: Retention, erasure, and export

The §9.3 / §17 data-lifecycle requirements: configurable retention *separated by
data class*, right-to-erasure, and data export.

| Area | Spec | Module |
|---|---|---|
| Per-data-class retention config (persisted) | §9.3 | `ccl/privacy/config.py` |
| Retention purge (redact raw early, keep trace + aggregates longer) | §9.3, §17 | `ccl/privacy/retention.py` |
| Right-to-erasure + export | §12, §17 | `ccl/privacy/export.py` |
| `RetentionPolicy` / `DeletionRequest` entities | §11 | `ccl/data/privacy_models.py` |
| Workspace gating (admin purge/config; own-or-admin erase/export) | §17 | `ccl/access/workspace.py` |

The separation is the point. A tutor message erodes in stages: after the raw
period its student text and reply are **redacted** while the policy/verifier
trace is kept; after the (longer) trace period the row is deleted entirely.
De-identified aggregate insights live longest, and the audit log is never
purged. Erasing a data subject removes their sessions, messages, and events but
leaves class-level patterns and other students' data intact — and the
administrative record of the erasure is itself retained.

---

## Hardening (Gate 4) — slice 3: Provider registry + runtime gate

The §15 / §17 / §10.5 model-governance requirement: an admin-visible record of
every vetted model and a runtime gate that fails closed.

| Area | Spec | Module |
|---|---|---|
| Provider record (region, retention, training-disabled, subprocessors, uses) | §11, §15 | `ccl/data/provider_models.py` |
| Register / approve / revoke, with an approval gate | §17 | `ccl/governance/registry.py` |
| Runtime `ensure_usable` gate wired into the orchestrator | §10.5, §17 | `ccl/tutor/orchestrator.py` |
| Workspace management (admin-only) | §17 | `ccl/access/workspace.py` |

Two rules are enforced. A provider that reserves the right to **train on student
data cannot be approved** (§17). And the runtime **fails closed**: the
orchestrator calls `ensure_usable` before handing anything to a model, so an
unregistered, unapproved, or revoked model can never reach students. Approval
records the `eval_run_id` that cleared Gate 3, tying a model change to evidence
(§10.5).

---

## Hardening (Gate 4) — slice 4: Prompt-injection battery

The §14 / §16 adversarial proving suite for both channels an injection can
arrive on: the student's message and the content of an approved document.

| Area | Spec | Module |
|---|---|---|
| Injection detection (text + retrieved chunks) | §8, §14 | `ccl/tutor/injection.py` |
| Runtime chunk scan + audited detection, fail-hard verification | §8, §17 | `ccl/tutor/orchestrator.py` |
| Worst-case model that obeys injections (for testing) | §16 | `ccl/providers/stub.py` |
| Proving suite (user-text + poisoned-document attacks) | §16 | `tests/test_injection_battery.py` |

The suite runs a deliberately gullible model — one that *obeys* injected
instructions — against a poisoned approved document and a malicious student
message. In both cases the runtime refuses to ship the attack: the verifier
fires and the orchestrator falls back to the safe message. The structural
reason it holds is that the policy decision is derived from the Curriculum
Contract, never from document or message text, so a poisoned chunk cannot change
which methods are forbidden. Detection and audit are the belt; the
contract-derived policy is the suspenders.

---

## The web app (§18, teacher-pilot scope)

A single-page app served by a standard-library HTTP server — no Node, no build,
no extra installs. It wraps the existing services and uses the live model when
`ANTHROPIC_API_KEY` is set.

| Surface | What it does | Module |
|---|---|---|
| **Studio** | Create a course, paste materials (auto-split into citable pages), compile a draft contract, edit the four method states + hint ceilings, approve the concept graph, and publish | `ccl/web/api.py`, `ccl/compiler/` |
| **Tutor** | The student experience with mode + attempt box, and a live **Governance panel** showing the hint ladder, citations, the policy rules that fired, and the verifier verdict on every reply | `ccl/web/api.py` |
| **Insights** | The weekly aggregate brief for a course | `ccl/web/api.py` |

The compiler seeds a draft from the uploaded materials — wiring source
references to real anchors, pre-filling ceilings and privacy defaults, and
generating the golden-case placeholders the publish gate needs — so the teacher
supplies only the pedagogy. Nothing publishes until the teacher approves the
concept graph, and the publish button surfaces the exact gate violations if the
contract isn't ready.

Scope note: this is the teacher-pilot slice of §18 (author a contract, watch the
tutor obey, read the brief). The department-coherence and full admin consoles are
not built — they aren't needed to put the core loop in front of one teacher.

## Not built yet (then pilot)

- **Accessibility pass** (§11.5, WCAG 2.2 AA) — a real audit of the UI.
- **Department-coherence and admin consoles** (§18) — beyond the teacher-pilot slice.
- **The pilot** (Gate 5) — no learning-harm signal, real student use,
  actionable teacher value, and a buyer willing to pay to continue.

The Gate-4 build (RBAC, retention/deletion, provider governance,
prompt-injection resilience) is complete, and Gate 3 passes on a live model. A
real privacy/security review belongs to the pilot phase.

## Notes on the reference stack

Targets FastAPI + Postgres/pgvector. These milestones run on SQLite with keyword
retrieval so the suite is fast and dependency-free; the invariants proven here
(tenant isolation, immutability, append-only audit, deterministic policy, and
verifier-enforced compliance) are storage- and retrieval-agnostic. pgvector
earns its place when semantic retrieval replaces the keyword retriever.
