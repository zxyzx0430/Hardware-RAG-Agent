"""Only calls that actually need a decision appear in the confirm event."""

from src.agent import hitl_permission


def test_ask_pending_calls_filters_auto_allowed(monkeypatch):
    calls = [
        {"name": "auto_allowed", "args": {}, "call_id": "c1"},
        {"name": "needs_ask", "args": {}, "call_id": "c2"},
    ]

    def classify(_classifier, call, _ctx):
        return "allow" if call["name"] == "auto_allowed" else "ask"

    monkeypatch.setattr(hitl_permission, "_classify_one_call", classify)

    selected = hitl_permission.ask_pending_calls(calls, "manual")

    assert [call["call_id"] for call in selected] == ["c2"]
