# pyright: reportUnknownVariableType=false, reportUnknownMemberType=false
"""Zhipu GLM provider session (experimental).

Uses the OpenAI Chat Completions-compatible v4 endpoint via the OpenAI SDK
(`base_url` override). Differences from the OpenAI Responses session:

- Prompt messages are already in OpenAI chat format, so they pass through
  as-is (images included, as ``image_url`` data URLs).
- Tools serialize to the chat-completions shape (``function`` wrapper).
- Thinking arrives as ``delta.reasoning_content`` on stream chunks.
- Tool images cannot ride inside ``role: "tool"`` messages, so multimodal
  parts are appended as a follow-up ``user`` message after the tool results.
- GLM pricing is not in MODEL_PRICING, so ``total_cost_usd`` returns None
  and the budget guard is skipped for these models.
"""

import base64
import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx
import openai
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

from agent.providers.base import (
    EventSink,
    ExecutedToolCall,
    ProviderSession,
    ProviderTurn,
    StreamEvent,
)
from costs.token_usage import TokenUsage
from agent.state import ensure_str
from agent.tools import CanonicalToolDefinition, ToolCall, parse_json_arguments
from config import ZHIPU_THINKING
from fs_logging.agent_runs import AgentRunRecorder
from fs_logging.prompt_reports import PromptReportLogger
from llm import Llm, get_zhipu_api_name

# Output cap sent on every request. Large single-file HTML easily needs
# 15-25k tokens; 32k leaves headroom while staying under common API caps.
ZHIPU_MAX_TOKENS = 32768

# The coding-plan gateway occasionally drops long streams mid-body when
# concurrent variants exceed the plan's concurrency limit; one stateless
# replay covers it (messages are not mutated until a turn completes).
ZHIPU_STREAM_RETRIES = 2


def serialize_zhipu_tools(
    tools: List[CanonicalToolDefinition],
) -> List[Dict[str, Any]]:
    serialized: List[Dict[str, Any]] = []
    for tool in tools:
        serialized.append(
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
        )
    return serialized


@dataclass
class ZhipuParseState:
    assistant_text: str = ""
    # Chat-completions tool fragments arrive keyed by chunk-local index.
    tool_calls: Dict[int, Dict[str, Any]] = field(default_factory=dict)
    turn_usage: Optional[TokenUsage] = None


def _attr(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _extract_zhipu_usage(chunk: Any) -> Optional[TokenUsage]:
    """Usage from the final stream chunk, if the endpoint provides one."""
    usage = _attr(chunk, "usage")
    if not usage:
        return None
    prompt_tokens = _attr(usage, "prompt_tokens", 0) or 0
    completion_tokens = _attr(usage, "completion_tokens", 0) or 0
    total_tokens = _attr(usage, "total_tokens", 0) or 0
    if not (prompt_tokens or completion_tokens or total_tokens):
        return None
    return TokenUsage(
        input=prompt_tokens,
        output=completion_tokens,
        cache_read=0,
        cache_write=0,
        total=total_tokens,
    )


async def _parse_chunk(
    chunk: Any,
    state: ZhipuParseState,
    on_event: EventSink,
) -> None:
    usage = _extract_zhipu_usage(chunk)
    if usage is not None:
        state.turn_usage = usage

    choices = _attr(chunk, "choices") or []
    if not choices:
        return
    delta = _attr(choices[0], "delta")
    if delta is None:
        return

    thinking = _attr(delta, "reasoning_content")
    if thinking:
        await on_event(StreamEvent(type="thinking_delta", text=ensure_str(thinking)))

    content = _attr(delta, "content")
    if content:
        state.assistant_text += ensure_str(content)
        await on_event(StreamEvent(type="assistant_delta", text=ensure_str(content)))

    fragments = _attr(delta, "tool_calls") or []
    for fragment in fragments:
        index = _attr(fragment, "index", 0) or 0
        entry = state.tool_calls.setdefault(
            index, {"id": None, "name": None, "arguments": ""}
        )
        fragment_id = _attr(fragment, "id")
        if fragment_id:
            entry["id"] = fragment_id
        function = _attr(fragment, "function")
        if function is not None:
            name = _attr(function, "name")
            if name:
                entry["name"] = name
            args_delta = _attr(function, "arguments")
            if args_delta:
                entry["arguments"] = ensure_str(entry["arguments"]) + ensure_str(
                    args_delta
                )
        if entry["arguments"]:
            await on_event(
                StreamEvent(
                    type="tool_call_delta",
                    tool_call_id=entry["id"] or f"zhipu-{index}",
                    tool_name=entry["name"],
                    tool_arguments=entry["arguments"],
                )
            )


def _build_provider_turn(state: ZhipuParseState) -> ProviderTurn:
    tool_calls: List[ToolCall] = []
    for index in sorted(state.tool_calls.keys()):
        entry = state.tool_calls[index]
        args, error = parse_json_arguments(entry.get("arguments"))
        if error:
            args = {"INVALID_JSON": ensure_str(entry.get("arguments"))}
        call_id = entry.get("id") or f"call-{uuid.uuid4().hex[:6]}"
        tool_calls.append(
            ToolCall(
                id=ensure_str(call_id),
                name=ensure_str(entry.get("name")) or "unknown_tool",
                arguments=args,
            )
        )
    return ProviderTurn(
        assistant_text=state.assistant_text,
        tool_calls=tool_calls,
        # The appended assistant message doubles as the native turn object.
        assistant_turn=None,
    )


class ZhipuProviderSession(ProviderSession):
    def __init__(
        self,
        client: AsyncOpenAI,
        model: Llm,
        prompt_messages: List[ChatCompletionMessageParam],
        tools: List[Dict[str, Any]],
        recorder: Optional[AgentRunRecorder] = None,
    ):
        self._client = client
        self._model = model
        self._tools = tools
        self._total_usage = TokenUsage()
        self._recorder = recorder
        self._prompt_report_logger = PromptReportLogger(
            provider="zhipu",
            model=model,
            api_model_name=get_zhipu_api_name(model),
        )
        # Messages are already in OpenAI chat format; pass through as-is.
        self._messages: List[ChatCompletionMessageParam] = list(prompt_messages)

    async def stream_turn(self, on_event: EventSink) -> ProviderTurn:
        api_model_name = get_zhipu_api_name(self._model)
        params: Dict[str, Any] = {
            "model": api_model_name,
            "messages": self._messages,
            "tools": self._tools,
            "tool_choice": "auto",
            "stream": True,
            "max_tokens": ZHIPU_MAX_TOKENS,
            # Zhipu-specific switch, passed through extra_body:
            # {"type": "enabled"} makes the model think (minutes per turn on
            # large screenshots); "disabled" goes straight to output.
            "extra_body": {
                "thinking": {"type": "enabled" if ZHIPU_THINKING else "disabled"}
            },
        }

        self._prompt_report_logger.record_request(params)
        if self._recorder is not None:
            self._recorder.record_llm_request("zhipu", api_model_name, params)

        state = ZhipuParseState()
        last_error: Optional[BaseException] = None
        for attempt in range(ZHIPU_STREAM_RETRIES):
            # Fresh parse state per attempt: a dropped stream may have already
            # emitted partial events; replaying the full turn re-emits them.
            state = ZhipuParseState()
            try:
                stream = await self._client.chat.completions.create(**params)  # type: ignore
                async for chunk in stream:  # type: ignore
                    await _parse_chunk(chunk, state, on_event)
                last_error = None
                break
            except (openai.APIConnectionError, httpx.HTTPError) as exc:
                # Only network-level failures are retried; API errors
                # (auth/quota/schema) would fail identically on replay.
                last_error = exc
                print(
                    f"[zhipu] stream attempt {attempt + 1} failed "
                    f"({type(exc).__name__}), retrying"
                )
        if last_error is not None:
            raise last_error

        if state.turn_usage is not None:
            self._prompt_report_logger.record_usage(state.turn_usage)
            self._total_usage.accumulate(state.turn_usage)

        turn = _build_provider_turn(state)
        if self._recorder is not None:
            self._recorder.record_llm_response(
                turn.assistant_text, turn.tool_calls, state.turn_usage
            )
        return turn

    def total_cost_usd(self) -> Optional[float]:
        # GLM is not in MODEL_PRICING; returning None opts out of the budget guard.
        return None

    @staticmethod
    def _image_url(part: Any) -> Optional[str]:
        if part.image_url:
            return part.image_url
        if part.data is not None:
            encoded = base64.b64encode(part.data).decode("ascii")
            return f"data:{part.mime_type};base64,{encoded}"
        return None

    async def append_tool_results(
        self,
        turn: ProviderTurn,
        executed_tool_calls: list[ExecutedToolCall],
    ) -> None:
        assistant_message: Dict[str, Any] = {"role": "assistant"}
        if turn.assistant_text:
            assistant_message["content"] = turn.assistant_text
        if turn.tool_calls:
            assistant_message["content"] = turn.assistant_text or None
            assistant_message["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments, ensure_ascii=False),
                    },
                }
                for call in turn.tool_calls
            ]
        self._messages.append(assistant_message)  # type: ignore[arg-type]

        image_parts: List[Dict[str, Any]] = []
        for executed in executed_tool_calls:
            result_json = json.dumps(executed.result.result, ensure_ascii=False)
            self._messages.append(
                {
                    "role": "tool",
                    "tool_call_id": executed.tool_call.id,
                    "content": result_json,
                }  # type: ignore[arg-type]
            )
            for part in executed.result.multimodal_parts or []:
                image_url = self._image_url(part)
                if image_url is None:
                    continue
                image_parts.append(
                    {"type": "image_url", "image_url": {"url": image_url}}
                )

        # Chat-completions tool messages are text-only, so images produced by
        # tools travel as a follow-up user message right after the results.
        if image_parts:
            self._messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "Images produced by the tool calls above are attached below.",
                        },
                        *image_parts,
                    ],
                }  # type: ignore[arg-type]
            )

    async def close(self) -> None:
        u = self._total_usage
        print(
            f"[TOKEN USAGE] provider=zhipu model={get_zhipu_api_name(self._model)} | "
            f"input={u.input} output={u.output} "
            f"cache_read={u.cache_read} cache_write={u.cache_write} "
            f"total={u.total} cost=unpriced"
        )
        await self._client.close()
