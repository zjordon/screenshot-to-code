# pyright: reportUnknownVariableType=false, reportUnknownMemberType=false
import copy
from typing import Any, Dict, List, Optional

import pytest

from agent.providers.base import ExecutedToolCall, ProviderTurn, StreamEvent
from agent.providers.zhipu import (
    ZhipuProviderSession,
    serialize_zhipu_tools,
)
from agent.tools import (
    CanonicalToolDefinition,
    ToolCall,
    ToolExecutionResult,
)
from agent.tools.types import ToolMultimodalPart
from llm import Llm


class _FakeChunkStream:
    def __init__(self, chunks: List[Dict[str, Any]]) -> None:
        self._chunks = chunks

    def __aiter__(self) -> "_FakeChunkStream":
        return self

    async def __anext__(self) -> Dict[str, Any]:
        if not self._chunks:
            raise StopAsyncIteration
        return self._chunks.pop(0)


class _FakeChatCompletions:
    def __init__(self, chunks: List[Dict[str, Any]]) -> None:
        self._chunks = chunks
        self.calls: List[Dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> _FakeChunkStream:
        self.calls.append(copy.deepcopy(kwargs))
        return _FakeChunkStream(list(self._chunks))


class _FakeChat:
    def __init__(self, completions: _FakeChatCompletions) -> None:
        self.completions = completions


class _FakeOpenAIClient:
    def __init__(self, chunks: List[Dict[str, Any]]) -> None:
        self.chat = _FakeChat(_FakeChatCompletions(chunks))

    async def close(self) -> None:
        return None


class _EventCollector:
    def __init__(self) -> None:
        self.events: List[StreamEvent] = []

    async def __call__(self, event: StreamEvent) -> None:
        self.events.append(event)


def _make_chunk(
    content: Optional[str] = None,
    reasoning_content: Optional[str] = None,
    tool_fragments: Optional[List[Dict[str, Any]]] = None,
    usage: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    delta: Dict[str, Any] = {}
    if reasoning_content is not None:
        delta["reasoning_content"] = reasoning_content
    if content is not None:
        delta["content"] = content
    if tool_fragments is not None:
        delta["tool_calls"] = tool_fragments
    chunk: Dict[str, Any] = {"choices": [{"delta": delta}]}
    if usage is not None:
        chunk["usage"] = usage
    return chunk


def _test_session(chunks: List[Dict[str, Any]]) -> tuple[
    ZhipuProviderSession, _FakeOpenAIClient
]:
    client = _FakeOpenAIClient(chunks)
    session = ZhipuProviderSession(
        client=client,  # type: ignore[arg-type]
        model=Llm.GLM_5_3_FLASH,
        prompt_messages=[
            {"role": "system", "content": "You build pages."},
            {"role": "user", "content": "Build a landing page."},
        ],
        tools=serialize_zhipu_tools(
            [
                CanonicalToolDefinition(
                    name="create_file",
                    description="Create the main HTML file.",
                    parameters={
                        "type": "object",
                        "properties": {"content": {"type": "string"}},
                        "required": ["content"],
                    },
                )
            ]
        ),
    )
    return session, client


@pytest.mark.asyncio
async def test_zhipu_streams_thinking_text_and_tool_fragments() -> None:
    session, client = _test_session(
        [
            _make_chunk(reasoning_content="Analyzing the screenshot"),
            _make_chunk(content="I will create the file."),
            _make_chunk(
                tool_fragments=[
                    {"index": 0, "id": "call-1", "function": {"name": "create_file"}}
                ]
            ),
            _make_chunk(
                tool_fragments=[
                    {
                        "index": 0,
                        "function": {"arguments": '{"content": "<html>hel'},
                    }
                ]
            ),
            _make_chunk(
                tool_fragments=[
                    {
                        "index": 0,
                        "function": {"arguments": 'lo</html>"}'},
                    }
                ]
            ),
            _make_chunk(
                usage={"prompt_tokens": 100, "completion_tokens": 200, "total_tokens": 300}
            ),
        ]
    )

    collector = _EventCollector()
    turn = await session.stream_turn(collector)

    assert turn.assistant_text == "I will create the file."
    assert len(turn.tool_calls) == 1
    tool_call = turn.tool_calls[0]
    assert tool_call.id == "call-1"
    assert tool_call.name == "create_file"
    assert tool_call.arguments == {"content": "<html>hello</html>"}

    event_types = [event.type for event in collector.events]
    assert "thinking_delta" in event_types
    assert "assistant_delta" in event_types
    assert event_types.count("tool_call_delta") >= 2
    tool_events = [e for e in collector.events if e.type == "tool_call_delta"]
    # Arguments arrive accumulated (partial JSON at each step).
    assert tool_events[-1].tool_arguments == '{"content": "<html>hello</html>"}'

    request = client.chat.completions.calls[0]
    assert request["model"] == "glm-5.3-flash"
    assert request["stream"] is True
    assert request["tools"][0]["type"] == "function"
    assert request["tools"][0]["function"]["name"] == "create_file"


@pytest.mark.asyncio
async def test_zhipu_invalid_json_arguments_become_invalid_json_marker() -> None:
    session, _ = _test_session(
        [
            _make_chunk(
                tool_fragments=[
                    {"index": 0, "id": "call-2", "function": {"name": "create_file"}}
                ]
            ),
            _make_chunk(
                tool_fragments=[
                    {"index": 0, "function": {"arguments": "{\"content\": "}}
                ]
            ),
        ]
    )

    turn = await session.stream_turn(_EventCollector())

    assert len(turn.tool_calls) == 1
    assert "INVALID_JSON" in turn.tool_calls[0].arguments


@pytest.mark.asyncio
async def test_zhipu_append_tool_results_with_images() -> None:
    chunks = [_make_chunk(content="done")]
    session, client = _test_session(chunks)

    first_turn = await session.stream_turn(_EventCollector())
    await session.append_tool_results(
        ProviderTurn(
            assistant_text=first_turn.assistant_text,
            tool_calls=[
                ToolCall(
                    id="call-9",
                    name="screenshot_preview",
                    arguments={},
                )
            ],
            assistant_turn=None,
        ),
        [
            ExecutedToolCall(
                tool_call=ToolCall(
                    id="call-9",
                    name="screenshot_preview",
                    arguments={},
                ),
                result=ToolExecutionResult(
                    ok=True,
                    result={"content": "Screenshots attached."},
                    summary={"status": "ok"},
                    multimodal_parts=[
                        ToolMultimodalPart(
                            display_name="preview_desktop.png",
                            mime_type="image/png",
                            data=b"\x89PNG fake bytes",
                        )
                    ],
                ),
            )
        ],
    )
    await session.stream_turn(_EventCollector())

    second_call = client.chat.completions.calls[1]
    messages = second_call["messages"]
    # assistant tool_calls → tool result → image-bearing user message
    assert messages[2]["role"] == "assistant"
    assert messages[2]["tool_calls"][0]["id"] == "call-9"
    assert messages[2]["tool_calls"][0]["function"]["name"] == "screenshot_preview"
    assert messages[3]["role"] == "tool"
    assert messages[3]["tool_call_id"] == "call-9"
    assert messages[4]["role"] == "user"
    content_parts = messages[4]["content"]
    assert content_parts[0]["type"] == "text"
    assert content_parts[1]["type"] == "image_url"
    assert content_parts[1]["image_url"]["url"].startswith("data:image/png;base64,")


@pytest.mark.asyncio
async def test_zhipu_total_cost_is_unpriced() -> None:
    session, _ = _test_session([_make_chunk(content="hi")])
    await session.stream_turn(_EventCollector())
    assert session.total_cost_usd() is None
