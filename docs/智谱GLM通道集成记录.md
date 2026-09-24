# 智谱 GLM Coding Plan 接入记录

> 日期: 2026-09-24 · 分支: `experiment/zhipu-glm`（基于 main @ `98595a3`）
> 目标: 让 screenshot-to-code 后端支持智谱 **GLM Coding Plan** 订阅，用 **glm-5.3-flash**（多模态）跑通「截图→代码」全链路

---

## 1. 背景与前置结论

### 1.1 为什么不能"纯配置"接入

本项目没有自定义模型入口，模型选择完全写死在后端（详见 `docs/agent-core/agent运行逻辑分析.md`）：

- 模型名单是 `Llm` 枚举（41 个 GPT/Claude/Gemini 变体，`backend/llm.py`）
- 场景→模型池硬编码在 `backend/routes/model_choice_sets.py`
- 前端设置里的模型选择器实际是摆设（后端参数抽取不读它）
- OpenAI 通道走 **Responses API**（`responses.create`），智谱 v4 只有 `chat/completions`——所以 `OPENAI_BASE_URL` 指向智谱必然 404

结论：必须写一个原生 Provider。

### 1.2 模型池对模型的硬性要求

| 要求 | 说明 | glm-5.3-flash |
|------|------|---------------|
| 多模态输入 | 图片模式下截图是全部需求信息，无文本兜底 | ✅ |
| 流式输出 | 三家 Provider 全部硬编码 stream | ✅ |
| 工具调用 | Agent 循环核心（无工具则退化为单发模式） | ✅ |
| 并行工具调用 | 加分项（一轮 `extract_assets`+`create_file` 同发） | ✅（实测） |

---

## 2. 排查过程（三个问题，三层修复）

### 2.1 问题一：接入即报"配额超限"

**现象**: UI 生成秒报 `OpenAI error - 'You exceeded your current quota...'`（4 路变体全挂）。

**排查**: 该文案是项目对 `openai.RateLimitError` 的罐头翻译，看不到细节。绕过 UI 直发 WS 请求复现（工具: `backend/_repro_ws_request.py`），再裸调智谱 API 看原始响应：

```
POST https://open.bigmodel.cn/api/paas/v4/chat/completions
-> 429 {"error":{"code":"1113","message":"余额不足或无可用资源包,请充值。"}}
```

**根因**: key 有效、模型名正确，但 **Coding Plan 订阅不走 `api/paas/v4` 按量付费端点**。

**端点探测结果**:

| 端点 | 结果 |
|------|------|
| `api/paas/v4/chat/completions`（按量付费） | ❌ 429 code=1113 余额不足 |
| **`api/coding/paas/v4/chat/completions`**（订阅专属） | ✅ **200**，OpenAI 兼容，响应含 `reasoning_content` |
| `api/anthropic`（Anthropic 兼容） | 未走通（探测路径问题），不再需要 |

**修复**: `config.py` 默认 `ZHIPU_BASE_URL` 改为 `https://open.bigmodel.cn/api/coding/paas/v4`。

### 2.2 问题二：第二轮流式响应被掐断

**现象**: 换端点后第 1 轮 LLM 调用成功（`input=6502 output=52`），第 2 轮（工具结果回填后）流到一半报 `httpx.RemoteProtocolError: peer closed connection without sending complete message body`。

**排查（单流对照实验）**: 单路直连、带 dashboard 截图、长输出——**8 分钟 / 32,590 个 chunk 完整跑完不断**。单流能活、4 路必断 → 元凶是 **Coding Plan 的并发限制**（4 路变体同时长流超出订阅并发额度，网关掐掉多余连接）。

**修复（双管齐下）**:
1. `NUM_VARIANTS` 改为环境变量可覆盖（`config.py`），`.env` 设 `NUM_VARIANTS=2`
2. `ZhipuProviderSession.stream_turn` 加断流重试（`ZHIPU_STREAM_RETRIES=2` 次，只重试 `openai.APIConnectionError`/`httpx.HTTPError` 网络类错误；轮内请求无状态，重放安全）

### 2.3 问题三：深度思考耗时过长

**现象（单流实验数据）**: glm-5.3-flash 默认深度思考——**纯 reasoning 输出 63,129 字符、耗时 345 秒才吐第一个正文 token**，整轮 476 秒。Agent 每轮如此则一次生成要烧掉大量时间与订阅配额。

**修复**: 请求经 `extra_body` 传 `{"thinking": {"type": "enabled"|"disabled"}}`，由环境变量 `ZHIPU_THINKING` 控制，**默认关闭**（flash 档位优先延迟）。想对比开启后的质量在 `.env` 设 `ZHIPU_THINKING=1`。

---

## 3. 最终实现（6 个代码文件 + 2 个测试文件）

### 3.1 接线总览

```
config.py            ZHIPU_API_KEY / ZHIPU_BASE_URL / ZHIPU_THINKING / NUM_VARIANTS(env)
        ↓
llm.py               GLM_5_3_FLASH 枚举 + MODEL_PROVIDER["zhipu"] + GLM_MODELS 集合
        ↓
model_choice_sets.py ZHIPU_ONLY_MODELS = (GLM_5_3_FLASH,)   ← 单条目池，按槽位循环
        ↓
generate_code.py     ModelSelectionStage: 三家 key 皆无时的兜底分支 elif ZHIPU_API_KEY
        ↓
factory.py           model ∈ GLM_MODELS → AsyncOpenAI(api_key, base_url) + ZhipuProviderSession
        ↓
agent/providers/zhipu.py   ★ 新增：Chat Completions 版 ProviderSession
```

### 3.2 ZhipuProviderSession 设计要点（`backend/agent/providers/zhipu.py`）

| 设计点 | 说明 |
|--------|------|
| 消息直通 | 项目内部规范本就是 OpenAI chat 格式，消息列表原样传递（图片 `image_url` data URL 直接可用），无需转换 |
| 工具序列化 | `serialize_zhipu_tools` 转成 chat completions 形态（`{"type":"function","function":{name,description,parameters}}`） |
| 思考流 | `delta.reasoning_content` → `StreamEvent(thinking_delta)` |
| 工具参数流 | 按 chunk 内 `index` 分桶累积 `function.arguments` 增量 → `tool_call_delta`（前端预览依赖） |
| 图片回填 | chat completions 的 `role:"tool"` 消息只支持文本——工具产图以**后续 user 消息 + image_url** 注入 |
| 用量 | 从末 chunk 的 `usage` 提取；GLM 不在 `MODEL_PRICING` → `total_cost_usd()` 返回 None |
| 预算护栏 | 未定价 = 一等语义，`$3` 熔断自动跳过（想启用需在 `costs/pricing.py` 补费率） |
| 输出上限 | `max_tokens=32768`（`ZHIPU_MAX_TOKENS` 常量，大 HTML 约 15-25k token） |
| 断流重试 | 见 2.2 |
| Key 来源 | 仅环境变量 `ZHIPU_API_KEY`（与 REPLICATE 同模式，**前端设置对话框配不了**） |

### 3.3 验证结果

- **单测**: 新增 `tests/test_zhipu_provider_session.py` 4 例（思考/正文/工具增量流解析、INVALID_JSON 自纠错、带图回填消息结构、未定价语义）；全量 **280 passed**；pyright 0 错误
- **附带修复**: `test_model_selection.py` 的"无 key 报错"用例加 monkeypatch 隔离 `ZHIPU_API_KEY`（开发者环境变量会泄入 pytest）
- **端到端**（01-shadcn-dashboard.png，2 变体）:

```
variantComplete × 2，WS 正常关闭 code=1000
15 次工具调用 · 12 次 setCode · 208 条正文流 · 508 条思考流
变体1: input=275,740 / output=8,217   ← 调用了 screenshot_preview 自检，截图回填致 input 暴涨
变体2: input=81,304  / output=6,635
```

变体1 的 27 万 input token 证明 **GLM 多模态在 agent 视觉自检回路上完全工作**（渲染→截图→回看→修正）。

---

## 4. 配置速查（`backend/.env` + 环境变量）

```bash
# Windows 用户级环境变量（已设置，新进程自动继承）
ZHIPU_API_KEY=<你的智谱Key>

# backend/.env
NUM_VARIANTS=2          # 变体数；恢复 4 路对比改回 4（注意订阅并发限制）
# ZHIPU_THINKING=1      # 取消注释开启深度思考（慢，质量待对比）
# ZHIPU_BASE_URL=...    # 一般不用动；默认订阅端点 api/coding/paas/v4
```

改动 `.env` 后需**重启后端**（config 在 import 时读取）。

---

## 5. 已知限制与调参建议

| 事项 | 现状 | 建议 |
|------|------|------|
| 预算熔断 | GLM 未定价，$3 护栏跳过 | 在 `costs/pricing.py` 的 `MODEL_PRICING` 加 `"glm-5.3-flash"` 费率即可启用 |
| 错误文案 | 智谱 429 被翻译成 OpenAI 罐头文案 | 可在 `_run_variant` 加 zhipu 分支透传原始 message |
| 并发 | 2 路稳定；4 路可能触发网关掐流（有重试兜底） | 以订阅档位实测为准 |
| 思考质量 | 默认关闭；开启后单轮 +~6 分钟 | 用 eval 系统（`/evals`）跑分对比后定夺 |
| 素材提取 | `extract_assets` 需 Gemini key，智谱-only 时不可用 | 模型用 `generate_images`/占位方案替代 |
| 视频模式 | 硬性要求 Gemini | 智谱-only 无法使用 |
| 前端 | 变体标签显示原始模型名 `glm-5.3-flash` | 如需友好名改 `frontend/src/lib/models.ts` |

---

## 6. 排查工具与测试资产

| 资产 | 用途 |
|------|------|
| `backend/_repro_ws_request.py` | 绕过 UI 直发 `/generate-code` WS 请求（读 `test-screenshots/01-*.png`），复现/定位问题首选 |
| `backend/_capture_test_shots.py` | 用后端 Playwright 抓任意网页为测试截图（视口 1280×832，TARGETS 列表可扩展） |
| `test-screenshots/` | 6 张分级测试图（auth ⭐ → dashboard ⭐⭐⭐ → hackernews/github-trending ⭐⭐⭐⭐⭐） |

**排障心法（本次实测有效）**:
1. 后端日志加 `PYTHONUNBUFFERED=1` 启动——否则 `print()` 重定向到文件是块缓冲，报错压在缓冲区看不到
2. UI 报错先看是不是罐头文案，用 `_repro_ws_request.py` 抓 WS 原始 error 消息
3. 拿不准端点/配额问题就裸调 API 看 status + body（429/1113 这类错误只有原始响应里可见）
4. 怀疑并发限制→做单流对照；怀疑时长限制→看断流发生的时间点

---

## 7. 复盘：集成路线选择

当时评估过两条路，最终选了方案二：

| | 方案一：Anthropic 兼容端点借壳 | 方案二：原生智谱通道（已实施） |
|---|---|---|
| 改动 | 0 行代码（env 三行） | 6 文件 + 测试 |
| 模型控制 | 智谱侧映射，不可控 | **精确锁定 glm-5.3-flash** |
| 协议适配 | 受制于智谱对 thinking/effort 参数的兼容性 | 自主掌控（思考开关、重试、图片回填方式） |
| 风险 | 参数不兼容即断路，难调 | 初期工作量大 |

事后看方案二的额外收益：端点探测中发现 Coding Plan 有**专属 OpenAI 兼容端点**（`api/coding/paas/v4`），走这条路完全绕开了 Anthropic 壳的参数兼容性赌博。
