# Phase 2: 流式 LLM 决策

> 对应 `_run_with_session` 每轮迭代的前半（engine.py:222-262）。

## 📋 Phase 概述

一轮迭代的"思考半场"：为本轮生成事件 ID、定义把流事件转译为前端消息的回调，然后调 `session.stream_turn` 让提供商流式产出——思考、正文、工具参数三类增量实时外送，结束后拿到结构化的 `ProviderTurn`。

### 🔄 主要逻辑流程

#### 1️⃣ **构建 on_event 回调** (第 222-260 行)
```python
assistant_event_id = self._next_event_id("assistant")
thinking_event_id = self._next_event_id("thinking")
async def on_event(event: StreamEvent) -> None: ...
```
- 每轮新建两类文本事件的 ID；闭包内按事件类型分派：录制 → 转发 assistant/thinking → 委托工具增量处理。

#### 2️⃣ **消费流式响应构建 ProviderTurn** (第 262 行)
```python
turn = await session.stream_turn(on_event)
```
- 提供商侧完成"原生流 → 归一事件 → 完整轮"的全部工作（解析状态机见各类职责文档）；引擎拿到的已是 `assistant_text + tool_calls`。

### 🔄 数据流

```
prompt + 原生会话容器
  │
  ├── on_event 回调 ──→ recorder.record_stream_event（全量落盘）
  │                 ──→ 前端 thinking/assistant 消息（带 eventId）
  │                 ──→ _handle_streamed_tool_delta（预览，见 Phase 4）
  │
  └── stream_turn ──→ ProviderTurn
                        ├── assistant_text: str
                        ├── tool_calls: List[ToolCall]
                        └── assistant_turn: 原生轮对象
```

## 返回值

本 Phase 产出 `turn: ProviderTurn`，交由 Phase 3 判定去向。

## 总结

1. 🔄 事件 ID 每轮重建——前端按 eventId 累积同源内容，跨轮不串流
2. 💬 回调即协议边界：Provider 只认 EventSink，不知道前端与记录器的存在
3. 📸 引擎视角极薄——两家解析状态机的复杂度全部封装在 Provider 侧

## 子步骤文档对照表

| 序号 | 子步骤名称 | 详细文档 | 状态 |
|------|-----------|---------|------|
| 1 | 构建 on_event 回调 | [1-构建on_event回调.md](./2-流式LLM决策内部逻辑/1-构建on_event回调.md) | ✅ |
| 2 | 消费流式响应构建 ProviderTurn | [2-消费流式响应构建ProviderTurn.md](./2-流式LLM决策内部逻辑/2-消费流式响应构建ProviderTurn.md) | ✅ |

## 详细文档

- [构建 on_event 回调](./2-流式LLM决策内部逻辑/1-构建on_event回调.md)
- [消费流式响应构建 ProviderTurn](./2-流式LLM决策内部逻辑/2-消费流式响应构建ProviderTurn.md)
