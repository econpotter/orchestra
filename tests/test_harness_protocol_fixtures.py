import json
from pathlib import Path


FIXTURES = Path(__file__).parent / "fixtures" / "harness_protocols"


def _events(name: str) -> list[dict]:
    return [json.loads(line) for line in (FIXTURES / name).read_text().splitlines()]


def test_codex_success_fixture_has_complete_structured_lifecycle():
    events = _events("codex-success.jsonl")

    assert [event["type"] for event in events] == [
        "thread.started",
        "turn.started",
        "item.completed",
        "turn.completed",
    ]
    assert events[0]["thread_id"] == "thread-fixture"
    assert json.loads(events[2]["item"]["text"]) == {
        "outcome": "ok",
        "note": "protocol-canary",
    }


def test_claude_auth_failure_fixture_does_not_trust_success_subtype():
    events = _events("claude-authentication-failure.jsonl")

    retries = [
        event for event in events
        if event.get("type") == "system" and event.get("subtype") == "api_retry"
    ]
    terminal = events[-1]
    assert retries and all(event["error"] == "authentication_failed" for event in retries)
    assert terminal["type"] == "result"
    assert terminal["subtype"] == "success"
    assert terminal["is_error"] is True
    assert terminal["api_error_status"] == 401


def test_fixtures_do_not_contain_capture_machine_paths_or_ids():
    for fixture in FIXTURES.glob("*.jsonl"):
        text = fixture.read_text()
        assert "/home/potterzot" not in text
        assert "/tmp/orchestra-harness-fixtures" not in text
        assert "econpotter" not in text
