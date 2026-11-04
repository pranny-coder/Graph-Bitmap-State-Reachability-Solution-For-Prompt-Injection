from types import SimpleNamespace

import pytest
from pydantic import BaseModel

# Field-level integration tests exercise the optional AgentDojo adapter.  Keep
# the core experiment suite runnable in a minimal Phase 2 environment.
pytest.importorskip("agentdojo", reason="requires optional AgentDojo integration")

from agentdojo.functions_runtime import FunctionCall
from phase3_agentdojo_gbsr import (
    GBSRToolsExecutor,
    _new_state,
)


class CalendarRecord(BaseModel):
    id_: str
    title: str
    description: str
    participants: list[str]
    all_day: bool


class SyntheticEmail(BaseModel):
    security_code: str
    body: str
    sender: str


class FakeRuntime:
    def __init__(
        self,
        result,
        error=None,
        tool_name="get_day_calendar_events",
    ):
        self.result = result
        self.error = error
        self.functions = {
            tool_name: SimpleNamespace(name=tool_name)
        }

    def run_function(self, env, name, args):
        return self.result, self.error


def execute_tool(tool_name, result, error=None):
    runtime = FakeRuntime(result, error, tool_name)
    state = _new_state()

    executor = GBSRToolsExecutor(
        sensitive_substrings=["email", "secret", "private"],
        untrusted_substrings=["calendar", "email"],
        risky_action_substrings=["send", "delete"],
    )

    call = FunctionCall(function=tool_name, args={})
    messages = [{"role": "assistant", "tool_calls": [call]}]

    _, _, _, _, extra = executor.query(
        "test",
        runtime,
        None,
        messages,
        {"gbsr_state": state},
    )
    return extra["gbsr_state"]


def test_calendar_fields_of_mixed_types_keep_provenance():
    result = [
        CalendarRecord(
            id_="c1",
            title="Team meeting",
            description="Discuss project updates",
            participants=["person@example.com"],
            all_day=False,
        )
    ]

    state = execute_tool("get_day_calendar_events", result)
    fields = [
        node for node in state["nodes"]
        if node["type"] == "data_field"
    ]

    assert len(fields) == 5
    assert all(node["influence_mask"] != 0 for node in fields)
    assert len({node["influence_mask"] for node in fields}) == 5
    assert all(node["sensitive_mask"] == 0 for node in fields)


def test_sensitive_email_fields_get_sensitive_labels():
    result = [
        SyntheticEmail(
            security_code="TEST-CODE",
            body="Synthetic test email body",
            sender="security@example.test",
        )
    ]

    state = execute_tool("search_emails", result)
    fields = {
        node["name"].split(":", 1)[1]: node
        for node in state["nodes"]
        if node["type"] == "data_field"
    }

    assert fields["$[0].security_code"]["sensitive_mask"] != 0
    assert fields["$[0].body"]["sensitive_mask"] != 0
    assert fields["$[0].sender"]["sensitive_mask"] == 0

    # All returned fields inherit untrusted-source labels from this policy.
    assert all(
        node["influence_mask"] != 0
        for node in fields.values()
    )


def test_failed_tool_call_does_not_create_data_field_nodes():
    state = execute_tool(
        "get_day_calendar_events",
        "",
        error=ValueError("Synthetic failure"),
    )

    assert not any(
        node["type"] == "data_field"
        for node in state["nodes"]
    )

    result_nodes = [
        node for node in state["nodes"]
        if node["type"] == "tool_result"
    ]

    assert len(result_nodes) == 1
    assert result_nodes[0]["sensitive_mask"] == 0
    assert result_nodes[0]["influence_mask"] == 0
