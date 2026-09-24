# GLM 实验取图与难度阶梯

> 日期: 2026-09-24 · 配套文档: [智谱GLM通道集成记录.md](./智谱GLM通道集成记录.md)
> 用途: 为「截图→代码」实验（glm-5.3-flash）准备测试素材，按难度由易到难推进

---

## 1. 推荐取图来源

| 站点 | 特点 | 适合测什么 |
|------|------|-----------|
| **ui.shadcn.com** | 本项目前端同款组件库的官方示例页 | 组件还原度（按钮/卡片/表单），起步热身 |
| **tailwindui.com** | Tailwind 官方组件预览 | 生成的代码是否走 Tailwind 路线 |
| **mobbin.com** | 海量真实 App 界面截图（移动端为主） | 移动端布局（记得切响应式友好的栈） |
| **onepagelove.com / land-book.com** | 落地页画廊 | 整页排版、Hero 区、分栏 |
| **godly.website / awwwards.com** | 高设计感网站截图 | 渐变/动效暗示/非常规布局（难题） |
| **dribbble.com** | 设计稿 mockup（非真实网页） | "设计稿→代码"场景，自带大量占位图 |
| **Figma Community 免费 UI Kit** | 浏览器里打开文件框选截图 | 项目 README 明说支持 Figma 设计稿，最贴近原始卖点 |
| **真实网站**（NYTimes / Hacker News / 任意后台系统） | 项目 README 自己的示例就是 NYTimes 和 HN | 密集信息页（地狱难度） |

⚠️ **实测坑（2026-09-24）**: shadcn 官网 `examples/cards`、`examples/forms`、`examples/music` 三个路由当前渲染为空（SPA 改版残留，页面主体仅 33 字符文本）——不要用。可用的示例路由: `examples/dashboard`、`examples/authentication`、`examples/playground`。

---

## 2. 截图技巧（直接影响还原质量）

1. **全页截图用 DevTools**: Chrome `F12` → `Ctrl+Shift+P` → 输入 `Capture full size screenshot`——比滚动+拼图干净，且生成的页面本来就是全页 HTML。
2. **别缩放**: 保持设备像素 1:1。缩小会丢文字细节，视觉识别直接受损。
3. **长页控制在 1~1.5 屏**: 太长的页面容易触发输出截断（当前 `ZHIPU_MAX_TOKENS=32768`）。
4. **多图输入**: 上传支持一次拖 **1~5 张**——同一站点的桌面+移动各截一张一起喂，可测多图理解。
5. **懒得手动截**: 用现成工具（见第 4 节），视口 1280×832 与项目 `screenshot_preview` 桌面规格一致。

---

## 3. GLM 实验难度阶梯

```
第 1 轮（链路验证）  : shadcn 组件页局部          —— 确认请求通、能出完整 HTML
第 2 轮（基础能力）  : onepagelove 上的简单落地页   —— 看布局/字体/间距还原
第 3 轮（组件复杂度）: 仪表盘类页面（表格+图表+侧栏）—— 看 CSS 细节崩不崩
第 4 轮（文本准确性）: 新闻列表/商品列表           —— 小字号密集文本是视觉模型常见弱项
第 5 轮（地狱模式）  : NYTimes 首页全页           —— 和 README 的 GPT/Claude 示例形成对照
```

每轮跑完看后端控制台 `[TOKEN USAGE] provider=zhipu` 行拿延迟/用量数据，配合前端变体横向对比。

---

## 4. 本地工具与现有测试资产

### 4.1 抓图工具

`backend/_capture_test_shots.py`（用后端 venv 现成的 Playwright，无需手动截图）:

```bash
cd backend && py -3.12 -m poetry run python _capture_test_shots.py
```

改脚本里的 `TARGETS` 列表（文件名, URL, 是否全页）即可扩充目标。

### 4.2 已生成的测试图（`test-screenshots/`，已 gitignore，可随时重建）

| 文件 | 大小 | 难度 | 测试重点 |
|------|------|------|---------|
| `03-shadcn-auth.png` | 93 KB | ⭐ | 表单、居中卡片布局——**建议第一张**跑链路验证 |
| `02-bing-home.png` | 1.4 MB | ⭐⭐ | 极简首页 + 大背景图（顺带测图片处理） |
| `01-shadcn-dashboard.png` | 226 KB | ⭐⭐⭐ | 侧栏 + 数据卡片 + 图表——组件还原度 |
| `05-shadcn-playground.png` | 85 KB | ⭐⭐⭐ | 模态/表单/下拉的组合交互页 |
| `04-hackernews-top.png` | 106 KB | ⭐⭐⭐⭐ | 密集文本列表——小字号识别 |
| `06-github-trending.png` | 339 KB | ⭐⭐⭐⭐⭐ | 密集列表 + 徽章 + 多栏信息——地狱难度 |

---

## 5. 实验注意事项

- **`extract_assets` 不可用**: 素材提取工具需要 Gemini key，智谱-only 时模型不会看到它——会用 `generate_images` 或占位方案替代，与 GPT/Claude 对比测试时把该差异考虑进去。
- **视频模式不可用**: 项目硬性要求 Gemini key。
- **并发限制**: Coding Plan 下保持 `NUM_VARIANTS=2`；调回 4 路需接受偶发掐流（有重试兜底，见集成记录 2.2 节）。
- **思考开关对比**: `ZHIPU_THINKING=1` 开启后单轮约 +6 分钟，第 2~5 轮值得做开/关对照评分。
