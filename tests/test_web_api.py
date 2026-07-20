"""Acceptance: the web API flow end to end (offline stub).

Exercises the functions the browser calls: initial state, course + material
creation, compile → publish (valid and invalid), tutoring, and the insight
brief. No HTTP server needed — the handlers are plain functions.
"""

from __future__ import annotations

import copy

from ccl.web import api


def _app():
    return api.AppState()


def test_initial_state_has_seeded_course():
    st = api.get_state(_app())
    assert "stub" in st["provider"].lower()  # offline in tests
    math = next(c for c in st["courses"] if c["id"] == "math_demo")
    assert math["published_version"] == 1
    assert any(m["id"] == "material_notes_03" for m in math["materials"])


def test_tutor_enforces_seeded_contract():
    app = _app()
    r = api.tutor(app, {"course_id": "math_demo", "mode": "practice",
                        "current_unit": "unit_3", "message": "Can I just use the cross product here?"})
    assert r["verifier_passed"] is True
    assert "rule_cross_product_not_yet" in r["policy_rules"]
    assert r["max_hint_level"] == 5
    assert r["outcome"] in ("answered", "revised")


def test_full_authoring_and_publish_flow():
    app = _app()
    assert "error" not in api.create_course(app, {"course_id": "bio_101", "name": "Biology"})
    api.add_material(app, {"course_id": "bio_101", "material_id": "cell_notes",
                           "title": "Cell notes", "text": "The cell is the unit of life.\n\nMitochondria make ATP."})
    draft = api.compile_draft(app, {"course_id": "bio_101", "title": "Biology 101"})["draft"]
    assert draft["source_policy"]["approved_material_ids"] == ["cell_notes"]

    # Unapproved concept graph is rejected with a clear, specific violation.
    bad = api.publish_contract(app, {"contract": copy.deepcopy(draft)})
    assert bad["published"] is False
    assert any(v["code"] == "concept_graph_not_approved" for v in bad["violations"])

    # Approve the graph and publish.
    draft["concept_graph_approved"] = True
    good = api.publish_contract(app, {"contract": draft})
    assert good["published"] is True

    st = api.get_state(app)
    bio = next(c for c in st["courses"] if c["id"] == "bio_101")
    assert bio["published_version"] == 1


def test_tutor_without_contract_is_guided_not_crashed():
    app = _app()
    api.create_course(app, {"course_id": "empty_course", "name": "Empty"})
    r = api.tutor(app, {"course_id": "empty_course", "mode": "practice", "message": "help"})
    assert "error" in r and "publish" in r["error"].lower()


def test_insights_returns_structure():
    app = _app()
    api.tutor(app, {"course_id": "math_demo", "mode": "practice", "message": "help with vectors",
                    "attempt": "I tried adding components"})
    r = api.insights(app, {"course_id": "math_demo"})
    assert "misconception_clusters" in r and "review_minutes" in r


def test_upload_text_file_and_activate():
    app = _app()
    api.create_course(app, {"course_id": "hist_1", "name": "History"})
    r = api.upload_material(app, {"course_id": "hist_1", "material_id": "ww1", "title": "WW1",
                                  "kind": "text", "text": "Causes.\n\nAlliances.\n\nTrench warfare."})
    assert r["ok"] and r["pages"] == 3


def test_upload_pdf_bad_data_is_handled():
    app = _app()
    r = api.upload_material(app, {"course_id": "math_demo", "material_id": "x", "title": "x",
                                  "kind": "pdf", "data_b64": "bm90YXBkZg=="})  # "notapdf"
    assert "error" in r


def test_simulate_class_populates_brief():
    app = _app()
    sim = api.simulate_class(app, {"course_id": "math_demo", "n": 6})
    assert sim["ok"] and sim["students"] == 6
    brief = api.insights(app, {"course_id": "math_demo"})
    clusters = brief["misconception_clusters"] + brief["full_solution_pressure"] + brief["prerequisite_gaps"]
    assert clusters, "expected the brief to surface a pattern after simulating a class"


def test_paste_without_id_or_title_works():
    app = _app()
    r = api.add_material(app, {"course_id": "math_demo", "text": "Just some notes.\n\nAnd more."})
    assert r["ok"] and r["pages"] == 2  # id/title defaulted, only text required


def test_paste_empty_text_is_rejected():
    app = _app()
    r = api.add_material(app, {"course_id": "math_demo", "text": "   "})
    assert "error" in r


def test_material_id_derived_from_title():
    app = _app()
    r = api.add_material(app, {"course_id": "math_demo", "title": "Chapter Two Notes",
                               "text": "Some content here."})
    assert r["ok"] and r["material_id"] == "chapter_two_notes"
    # A second material with the same title gets a distinct id, not a clash.
    r2 = api.add_material(app, {"course_id": "math_demo", "title": "Chapter Two Notes",
                                "text": "More content."})
    assert r2["material_id"] != r["material_id"]


def test_insights_activity_block_always_present():
    app = _app()
    api.tutor(app, {"course_id": "math_demo", "mode": "practice", "message": "help with vectors"})
    r = api.insights(app, {"course_id": "math_demo"})
    assert "activity" in r
    assert r["activity"]["total_questions"] >= 1
    assert r["activity"]["distinct_students"] >= 1


def test_attempt_image_counts_as_attempt():
    app = _app()
    base = {"course_id": "math_demo", "mode": "practice", "current_unit": "unit_3",
            "message": "just give me the answer"}
    no_attempt = api.tutor(app, dict(base))
    with_image = api.tutor(app, dict(base, attempt_image={"media_type": "image/png", "data_b64": "iVBORw0KGgo="}))
    # An attached photo should unlock more help than no attempt at all.
    assert with_image["hint_level"] >= no_attempt["hint_level"]


def test_role_separation_permissions():
    from ccl.access import Permission
    app = _app()
    teacher = app.principal("teacher")
    student = app.principal("student")
    # Teacher endpoints
    for p in (Permission.CONTRACT_AUTHOR, Permission.CONTRACT_PUBLISH,
              Permission.MATERIAL_IMPORT, Permission.INSIGHT_VIEW):
        assert app.controller.can(teacher, p)
        assert not app.controller.can(student, p)
    # Tutor is usable by the student; the teacher can preview it.
    assert app.controller.can(student, Permission.TUTOR_USE)
    assert app.controller.can(teacher, Permission.TUTOR_PLAYGROUND)


def test_student_state_hides_unpublished_and_details():
    app = _app()
    api.create_course(app, {"course_id": "draft_course", "name": "Draft"})
    ss = api.student_state(app)
    ids = [c["id"] for c in ss["courses"]]
    assert "math_demo" in ids          # published seed course is visible
    assert "draft_course" not in ids   # unpublished course is hidden
    # No materials, sources, versions, or analytics leak into student state.
    assert all(set(c.keys()) <= {"id", "name"} for c in ss["courses"])


def test_student_role_cannot_reach_teacher_permissions():
    from ccl.access import Permission
    app = _app()
    student = app.principal("student")
    teacher = app.principal("teacher")
    assert not app.controller.can(student, Permission.CONTRACT_PUBLISH)
    assert not app.controller.can(student, Permission.INSIGHT_VIEW)
    assert app.controller.can(student, Permission.TUTOR_USE)
    assert app.controller.can(teacher, Permission.CONTRACT_PUBLISH)
