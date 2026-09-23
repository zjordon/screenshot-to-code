# 构建 on_event 回调（每轮迭代开头）

> 源码: `backend/agent/engine.py:222-260`（`_run_with_session` 循环体的第一段）

### 📋 **方法作用**

为当前轮定义"流事件如何变成记录与前端消息"的转译规则：生成事件 ID、组装录制分支与三类转发分支——Provider 的每一个增量都经此回调流出引擎。

---

### 🔄 **主要逻辑流程**

#### **1️⃣ 事件 ID 生成（第 222-225 行）**

```python
assistant_event_id = self._next_event_id("assistant")
thinking_event_id = self._next_event_id("thinking")
started_tool_ids: set[str] = set()
streamed_lengths: Dict[str, int] = {}
```

**关键参数说明：**
| 变量 | 格式 | 用途 |
|------|------|------|
| `*_event_id` | `{前缀}-{variant_index}-{hex8}` | 前端按 eventId 累积同一段思考/正文；每轮新建避免跨轮内容串流 |
| `started_tool_ids` | 每轮重建 | 记录已发过 toolStart 的工具调用（流式预告与正式执行去重） |
| `streamed_lengths` | 每轮重建 | 每个工具调用已预览的内容长度（增量节流） |

#### **2️⃣ 回调三分支（第 227-260 行）**

```python
async def on_event(event: StreamEvent) -> None:
    # 分支 0：录制（所有事件）
    if self.recorder is not None:
        if event.type == "assistant_delta":
            stream_event_id = assistant_event_id
        elif event.type == "thinking_delta":
            stream_event_id = thinking_event_id
        else:
            stream_event_id = event.tool_call_id
        self.recorder.record_stream_event(event, stream_event_id)

    # 分支 1：正文
    if event.type == "assistant_delta":
        if event.text:
            await self._send("assistant", event.text, event_id=assistant_event_id)
        return

    # 分支 2：思考
    if event.type == "thinking_delta":
        if event.text:
            await self._send("thinking", event.text, event_id=thinking_event_id)
        return

    # 分支 3：工具参数增量 → 预览处理（Phase 4 详述）
    if event.type == "tool_call_delta":
        await self._handle_streamed_tool_delta(event, started_tool_ids, streamed_lengths)
```

**验证逻辑：**
- 空文本增量（`if event.text`）直接不发——避免占位消息；
- 录制**先于**转发：宁可前端丢消息也不丢档案；
- 工具增量用 `tool_call_id` 本身作 eventId——与后续正式执行的 toolStart/toolResult 共用同一 ID，前端归并为一个活动条目。

---

### 🎯 **设计亮点**

1. **闭包即适配器**：Provider 只依赖 `EventSink` 类型别名，引擎把"录制+转发+预览"三职责织进一个闭包——没有中间队列、没有额外协程。
2. **两类 ID 策略**：文本流用"轮级新建 ID"（同轮同源累积），工具流用"调用级原生 ID"（跨阶段关联）——同一机制服务两种前端渲染语义。

---

### 📊 **返回值结构**

| 字段 | 类型 | 说明 |
|------|------|------|
| `on_event` | `EventSink (Callable)` | 传给 `session.stream_turn` 的唯一回调 |

---

### 💡 **典型使用场景**

- 前端 AgentActivity 面板的 thinking/assistant 气泡逐字增长即由此驱动。

---

### 🔗 **与其他方法的协作**

```
_run_with_session (每轮)
  │
  ▼
on_event 闭包
  ├── recorder.record_stream_event   （档案）
  ├── _send → WebSocketCommunicator  （前端）
  └── _handle_streamed_tool_delta    （预览，engine.py:170-216）
```

---

## 📂 **相关文件**

| 文件 | 作用 |
|------|------|
| `backend/agent/engine.py:222-260` | 本段 |
| `backend/agent/providers/base.py:37` | EventSink 定义 |
| `backend/fs_logging/agent_runs.py:427-468` | 录制分支实现 |
