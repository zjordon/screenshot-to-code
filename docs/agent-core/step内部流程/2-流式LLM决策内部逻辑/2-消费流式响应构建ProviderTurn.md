# 消费流式响应构建 ProviderTurn — `stream_turn` 的引擎视角

> 调用点: `backend/agent/engine.py:262`；实现在三家 Provider（详见各自类职责文档）

### 📋 **方法作用**

发起一轮 LLM 请求并全程消费其流式输出：引擎只写一行调用，但 Provider 侧完成的"请求组装 → 流解析 → 轮构建"三段工作是理解本步骤的关键。

---

### 🔄 **主要逻辑流程**

#### **1️⃣ 引擎侧调用（第 262 行）**

```python
turn = await session.stream_turn(on_event)
```

- 无超时、无重试——失败即异常上抛到 `_run_variant` 的异常翻译层。

#### **2️⃣ Provider 侧三段（以共性描述，差异见下表）**

```
① 请求组装
     recorder.record_llm_request + prompt_report.record_request（先落盘）
     按厂商组参（思考档位/缓存策略/max_output_tokens=50000）
② 流消费
     async for 原生流 → 解析状态机 → on_event(归一 StreamEvent)
     （解析状态机：OpenAIResponsesParseState / AnthropicParseState / GeminiParseState）
③ 轮构建
     usage 提取与口径归一 → total_usage.accumulate
     定稿 tool_calls（parse_json_arguments，失败包 INVALID_JSON）
     return ProviderTurn(assistant_text, tool_calls, assistant_turn=原生轮)
```

**三家差异速查：**

| 维度 | OpenAI | Anthropic | Gemini |
|------|--------|-----------|--------|
| API | responses.create(stream) | messages.stream | generate_content_stream |
| 思考 | reasoning.effort + summary auto | adaptive + output_config.effort | thinking_level + include_thoughts |
| 工具参数流 | 累积快照（需差分） | partial_json 增量 | 一次到位（dict） |
| 原生轮 | output_items 列表 | final_message | Content（含 thought signature） |
| 缓存 | prompt_cache_retention(24h, 仅 5.4) | cache_control ephemeral | 隐式 |

---

### 🎯 **设计亮点**

1. **引擎对厂商零感知**：本步骤是协议抽象收益最直观处——引擎的一行代码背后是三套完全不同的流协议。
2. **请求先记录后发出**：`record_request` 在 API 调用之前落盘，崩溃也留现场。

---

### 📊 **返回值结构**

| 字段 | 类型 | 说明 |
|------|------|------|
| `turn.assistant_text` | `str` | 本轮正文（可能为空——纯工具轮） |
| `turn.tool_calls` | `List[ToolCall]` | 定稿后的工具调用（参数已 dict 化或带 INVALID_JSON） |
| `turn.assistant_turn` | `Any` | 厂商原生轮，Phase 5 回填必需 |

---

### 💡 **典型使用场景**

- 典型轮：思考若干 → 零星正文 → 1-3 个工具调用（extract_assets + create_file 常见）。
- 最终轮：只有正文（总结语），`tool_calls` 为空 → 触发 Phase 3 终结化。

---

### 🔗 **与其他方法的协作**

```
AgentEngine._run_with_session
  │ 一行调用
  ▼
ProviderSession.stream_turn
  ├── record_llm_request / record_llm_response（Recorder）
  ├── PromptReportLogger.record_request / record_usage
  ├── 解析状态机 → on_event（引擎闭包）
  └── TokenUsage.accumulate
        │
        ▼
  ProviderTurn → Phase 3 终止判定
```

---

## 📂 **相关文件**

| 文件 | 作用 |
|------|------|
| `backend/agent/engine.py:262` | 调用点 |
| `backend/agent/providers/openai.py:444-478` | OpenAI 实现 |
| `backend/agent/providers/anthropic/provider.py:355-408` | Anthropic 实现 |
| `backend/agent/providers/gemini.py:320-378` | Gemini 实现 |
