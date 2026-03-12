from app.services.guardrails import IntermediateService


def test_intermediate_service_marks_referential_followup_without_history():
    service = IntermediateService()

    frame = service.build(
        {
            "route": "CHAT",
            "messages": [type("M", (), {"content": "what about it?"})()],
            "metadata": {},
        }
    )

    assert frame["route"] == "CHAT"
    assert "referent" in frame["unknowns"]
    assert frame["notes"]["referential_followup"] is True


def test_intermediate_service_requires_sql_evidence_for_chat_lookup_question():
    service = IntermediateService()

    frame = service.build(
        {
            "route": "CHAT",
            "messages": [type("M", (), {"content": "show my tasks"})()],
            "metadata": {},
        }
    )

    assert frame["notes"]["question_type"] == "lookup"
    assert frame["required_evidence"] == ["sql_rowset"]
    assert frame["notes"]["requires_data_evidence"] is True
