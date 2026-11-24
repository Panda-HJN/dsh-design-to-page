# dsh-design-to-page

DSH（DeepSeek Harness）bundle：设计导出物（PNG + 坐标标注 JSON）→ 可维护页面，附带可度量的像素回归。

四个工具：`design_ir`、`design_shot`、`design_diff`、`design_measure`。详见 [docs/DETAIL.md](docs/DETAIL.md)（中文）/ [docs/DETAIL.en.md](docs/DETAIL.en.md)。

## 安装

```bash
# 从 npm
plugin_manager install_bundle target=dsh-design-to-page@0.0.3

# 或从本地目录
plugin_manager install_bundle target=/abs/path/to/dsh-design-to-page
```

## 运行要求

- Node ≥ 22（`design_shot` 用内置 WebSocket 直连 CDP，不需要 Playwright）
- Python 3.10+ 与 Pillow；`design_diff` **还需要 numpy**（见 requirements.txt）
- 本机有 Chromium 内核浏览器（Chrome / Edge / Chromium 任一）

## 边界

- 不下载浏览器、不依赖 Playwright。
- 不替你判断装饰件语义与文本大小写；这些仍需对照 PNG。
- 像素阈值随项目重新标定，不要照搬示例值。
- `.design-to-page.json` 可以指定可执行文件路径，只在你信任的项目里使用。
