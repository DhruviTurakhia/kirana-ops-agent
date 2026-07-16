from __future__ import annotations

from pathlib import Path

from agents import (
    Agent,
    ModelSettings,
    RunConfig,
    Runner,
    SQLiteSession,
    ToolExecutionConfig,
    set_default_openai_key,
)

from kirana_agent.agent.prompt import store_instructions
from kirana_agent.agent.tools import ALL_TOOLS, AgentContext


class StoreAgentRuntime:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        session_database_path: str | Path,
    ):
        set_default_openai_key(api_key)
        self.session_database_path = Path(session_database_path)
        self.session_database_path.parent.mkdir(parents=True, exist_ok=True)
        self.agent: Agent[AgentContext] = Agent(
            name="Kirana Store Operations",
            model=model,
            instructions=store_instructions,
            tools=ALL_TOOLS,
            model_settings=ModelSettings(parallel_tool_calls=False),
        )

    async def run_turn(
        self,
        *,
        message: str,
        context: AgentContext,
        session_id: str,
    ) -> tuple[str, dict[str, int]]:
        session = SQLiteSession(session_id, db_path=self.session_database_path)
        result = await Runner.run(
            self.agent,
            message,
            context=context,
            session=session,
            max_turns=18,
            run_config=RunConfig(
                workflow_name="Kirana Telegram Turn",
                group_id=session_id,
                trace_include_sensitive_data=False,
                trace_metadata={
                    "channel": "telegram",
                    "chat_id": context.chat_id,
                    "source_event_id": context.source_event_id,
                },
                tool_execution=ToolExecutionConfig(max_function_tool_concurrency=1),
                tool_not_found_behavior="return_error_to_model",
            ),
        )
        usage = result.context_wrapper.usage
        usage_summary = {
            "requests": usage.requests,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "total_tokens": usage.total_tokens,
        }
        return str(result.final_output), usage_summary
