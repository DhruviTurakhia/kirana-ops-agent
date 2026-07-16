from __future__ import annotations

import json
from types import SimpleNamespace

from kirana_agent.agent.prompt import store_instructions
from kirana_agent.agent.runtime import StoreAgentRuntime
from kirana_agent.agent.tools import ALL_TOOLS, AgentContext

EXPECTED_TOOL_NAMES = {
    "search_products",
    "get_stock",
    "list_low_stock",
    "list_tax_rules",
    "create_product",
    "receive_stock",
    "get_open_bill",
    "start_bill",
    "patch_bill",
    "set_bill_payment",
    "set_bill_customer",
    "refresh_bill",
    "preview_bill",
    "finalize_bill",
    "cancel_bill",
    "get_bill",
    "list_recent_bills",
    "search_customers",
    "create_customer",
    "get_khata_balance",
    "get_khata_statement",
    "record_khata_charge",
    "record_khata_payment",
    "get_preferences",
    "set_preference",
    "clear_preference",
    "get_daily_summary",
    "close_day",
    "generate_invoice_pdf",
    "generate_sales_deck",
}


def _assert_strict_objects(node) -> None:
    if isinstance(node, dict):
        if "pattern" in node:
            assert "(?" not in node["pattern"], "OpenAI tool schemas reject regex lookaround"
        if node.get("type") == "object" and "properties" in node:
            assert node.get("additionalProperties") is False
            assert set(node.get("required", [])) == set(node["properties"])
        for value in node.values():
            _assert_strict_objects(value)
    elif isinstance(node, list):
        for value in node:
            _assert_strict_objects(value)


def test_all_thirty_tools_import_with_unique_strict_schemas() -> None:
    assert len(ALL_TOOLS) == 30
    assert {tool.name for tool in ALL_TOOLS} == EXPECTED_TOOL_NAMES
    assert len({tool.name for tool in ALL_TOOLS}) == len(ALL_TOOLS)

    for tool in ALL_TOOLS:
        assert tool.description and tool.description.strip()
        assert tool.strict_json_schema is True
        schema = tool.params_json_schema
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        assert "ctx" not in schema.get("properties", {})
        json.dumps(schema)
        _assert_strict_objects(schema)


def test_prompt_includes_trusted_state_and_operating_contract(store) -> None:
    store.service.set_preference(
        owner_id="owner-test",
        source_event_id="prompt-preference",
        key="default_payment_mode",
        value="UPI",
    )
    draft = store.service.start_bill_draft(
        owner_id="owner-test",
        chat_id="prompt-chat",
        source_event_id="prompt-draft",
    )
    context = AgentContext(
        service=store.service,
        invoice_generator=None,
        deck_generator=None,
        owner_id="owner-test",
        chat_id="prompt-chat",
        source_event_id="prompt-turn",
    )

    prompt = store_instructions(SimpleNamespace(context=context), None)

    assert draft["id"] in prompt
    assert '"default_payment_mode": "UPI"' in prompt
    assert "Ground every operational fact" in prompt
    assert "Stock changes only through finalize_bill" in prompt
    assert "A payment never creates a customer" in prompt


def test_runtime_constructs_agent_without_running_model(monkeypatch, tmp_path) -> None:
    captured = []
    monkeypatch.setattr(
        "kirana_agent.agent.runtime.set_default_openai_key", captured.append
    )
    session_path = tmp_path / "sessions" / "agent.sqlite3"

    runtime = StoreAgentRuntime(
        api_key="offline-test-key",
        model="offline-test-model",
        session_database_path=session_path,
    )

    assert captured == ["offline-test-key"]
    assert runtime.session_database_path == session_path
    assert session_path.parent.is_dir()
    assert runtime.agent.name == "Kirana Store Operations"
    assert runtime.agent.model == "offline-test-model"
    assert runtime.agent.instructions is store_instructions
    assert runtime.agent.tools == ALL_TOOLS
    assert runtime.agent.model_settings.parallel_tool_calls is False
