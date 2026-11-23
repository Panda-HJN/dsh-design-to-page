# 设计稿还原（Design to page）

把设计导出物（PNG + 坐标标注 JSON）还原成可维护页面，核心是**可度量的像素闭环**，不是"看图写页面"。

## 工具

| 工具 | 做什么 |
|---|---|
| `design_ir` | 标注 JSON → IR：布局文法（grid / flex-row / flex-col / overlay / stack / absolute，带证据与置信）、结构语义（分割线、图标簇）、同构组件聚类与实例数据、图片槽、装饰候选、设计不变量（版心 / 主 grid / 行容器内边距 / 圆角）、遮罩矩形；带 `png` 时核查每个文本节点是否真的渲染，直接列出隐藏图层 |
| `design_shot` | 用 Chrome DevTools Protocol 截图，视口与 DPR 精确可控，零 npm 依赖；返回最终 URL、scrollHeight、`[data-slot]` 矩形、指定选择器的盒模型；落到 `/login` 或路径不符即失败 |
| `design_diff` | 原生 2x 像素回归：MAE、>10、>40、位移搜索、分区统计、热力图、`metrics.json`，可设阈值作门禁 |
| `design_measure` | 裁剪放大、3×3 邻域取色、区域平均色、设计-实现同框并排 |

## 项目契约

在项目根放可选的 `.design-to-page.json`；工具参数优先级更高。

```json
{
  "boardWidth": 1920,
  "boardHeight": 1080,
  "pngScale": 2,
  "irOut": "design-ir",
  "pythonPath": "/path/to/python",
  "browserPath": "/path/to/chrome",
  "pages": {
    "/route/a": { "boardHeight": 1080 },
    "/route/b": { "boardHeight": 2400, "irOut": "design-ir-b" }
  }
}
```

- `pages` 按路由覆盖画板尺寸；`design_shot` 从 URL 路径取，`design_diff` 从 `page` 参数取。
- `pythonPath` / `browserPath` 缺省时自动探测（DSH 运行时、系统 Chrome/Edge/Chromium）。
- 契约文件带 BOM 也能读。

**信任边界**：契约文件可以指定工具将要执行的可执行文件。只在信任的项目里使用；拿到别人的仓库先看这份契约再跑。

## 循环

1. `design_ir`（有 PNG 就带上）→ 读 `report.md`；只复核置信 <0.8 的容器；被标为隐藏图层的文本不要画。
2. 按 `invariants.json` 写 token，按 `layout.json` 写结构（只写约束，不搬 left/top）。
3. `design_shot` 按画板尺寸与倍率截图。
4. `design_diff`：**先看位移** → `>40`（结构）→ `>10`（抗锯齿）；每次只改一处。
5. `design_measure` 单卡/图标并排看细节。
6. 收尾跑项目自身的 lint / type-check / build 门禁。

## 运行前提

- Node ≥ 22（截图器用内置 WebSocket）。
- Python 3.10+ 与 Pillow（`design_ir`、`design_measure`）；**`design_diff` 还需要 numpy**。
- 本机有 Chromium 内核浏览器；不下载浏览器、不依赖 Playwright。

## 已知边界

- 导出文本的大小写可能与渲染不一致，文本以 PNG 为准。
- 图片内容不在 JSON 里，只有 `basic.type == "image"` 标记；从 PNG 裁或用平均色块占位。
- 装饰件只给候选，需要人或模型确认。
- 隐藏图层检测基于文本 bbox 内的暗像素，检测不了"隐藏的纯色块"。
- 像素阈值按项目重新标定，不要照搬示例值。

## skill

`skill/SKILL.md` 是给人/agent 的执行剧本。DSH 目前不会自动注册 bundle 内的 skill，需要复制到技能目录（如 `~/.dsh/skills/design-to-page/`）后使用。
