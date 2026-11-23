# dsh-design-to-page

Unofficial community plugin for [DSH](https://github.com/deepseek-ai/deepseek-harness). Not affiliated with or endorsed by the DSH project.

Turn a design export (PNG + coordinate annotation JSON) into a maintainable page, with a measurable pixel loop instead of "look at a picture and write markup".

## Tools

| Tool | What it does |
|---|---|
| `design_ir` | Annotation JSON → IR: layout grammar per container (grid / flex / overlay / absolute) with evidence and confidence, structural semantics, component clustering, image slots, design invariants, mask rectangles; with a PNG it also catches hidden layers |
| `design_shot` | Screenshot via the Chrome DevTools Protocol with exact viewport and DPR. No Playwright, no npm dependencies |
| `design_diff` | Native-2x pixel regression: MAE, >10, >40, shift search, regions, heatmap, optional gate |
| `design_measure` | Crop with zoom, color sampling, region average, side-by-side pairs |

## Install

```bash
plugin_manager install_bundle target=dsh-design-to-page@0.0.1
```

Or from a local checkout:

```bash
plugin_manager install_bundle target=/path/to/repo/packages/dsh-design-to-page
```

## Requirements

- Node ≥ 22 (the screenshot tool uses the built-in WebSocket)
- Python 3.10+ with Pillow; `design_diff` additionally needs numpy
- A Chromium-based browser installed locally

## Repository layout

```
packages/dsh-design-to-page/    the npm package and DSH bundle
  index.js                      tool registration
  scripts/                      Python and Node implementations
  skill/SKILL.md                agent playbook
  docs/                         usage, contract, limits
examples/bare-scaffold/         empty Vite + Vue + Tailwind + Element Plus project
```

The scaffold is deliberately empty: it ships no design files, no brand assets and no business copy.
Restored pages live in your own project, not in this repository.

## License

MIT — see [LICENSE](LICENSE).
