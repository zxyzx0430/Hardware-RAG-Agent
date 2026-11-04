"""Synthetic model responses to the strict contract, never a production bypass."""
import json


def structured_response(prompt):
    records = [json.loads(line) for line in prompt.splitlines() if line.startswith('{"role":')]
    if not records:
        raise ValueError("No complete source records in test prompt")
    first_index, first = next((index, record) for index, record in enumerate(records, 1) if record["content"].strip())
    last = records[-1]
    source = first.get("source", f"s{first_index}")
    last_source = last.get("source", f"s{len(records)}")
    quote = first["content"][-100:]
    last_quote = last["content"][-100:]
    return json.dumps({
        "goal": [{"text": quote, "source": source}],
        "constraints": [{"text": quote, "source": source}],
        "completed": [],
        "current_state": [{"text": last_quote, "source": last_source}],
        "decisions": [], "failed_attempts": [], "open_questions": [],
        "artifacts_and_evidence": [{"text": quote, "source": source}],
        "next_action": [{"text": last_quote, "source": last_source}],
    }, ensure_ascii=False, separators=(",", ":"))
