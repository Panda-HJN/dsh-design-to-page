# Design to page

Turn a design export (PNG + coordinate annotation JSON) into a maintainable page, with a measurable pixel loop instead of "look at a picture and write markup".

## Tools

| Tool | What it does |
|---|---|
| `design_ir` | Annotation JSON → IR: per-container layout grammar (grid / flex-row / flex-col / overlay / stack / absolute) with evidence and confidence, structural semantics (separators, icon clusters), component clustering with instance data, image slots, decoration candidates, design invariants (gutter, main grid, row inset, radii), mask rectangles. With `png`, it also checks whether each text node actually renders and lists hidden layers. |
| `design_shot` | Screenshot through the Chrome DevTools Protocol with exact viewport and DPR. No npm dependencies. Returns the final URL, scrollHeight, `[data-slot]` rectangles, selected element boxes. Fails on `/login` or a path mismatch. |
| `design_diff` | Native-2x pixel regression: MAE, >10, >40, shift search, per-region stats, heatmap, `metrics.json`, optional gate. |
| `design_measure` | Crop with zoom, 3×3 color sampling, region average color, design-vs-implementation side-by-side pairs. |

## Project contract

Optional `.design-to-page.json` at the project root. Tool arguments win over it.

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

- `pages` overrides board size per route; `design_shot` resolves it from the URL path and `design_diff` from its `page` argument.
- `pythonPath` and `browserPath` are auto-detected when omitted.
- A file with a BOM is accepted.

**Trust boundary:** the contract can name executables that the tools will run. Only use these tools in projects you trust, and review the contract file of a cloned repository before running anything.

## Loop

1. Run `design_ir` (pass `png` when available) and read `report.md`. Verify only containers with confidence < 0.8; never draw text reported as a hidden layer.
2. Write tokens from `invariants.json` and structure from `layout.json`. Express constraints (grid/flex/gap), never `left`/`top`.
3. `design_shot` at the design's board size and scale.
4. `design_diff`: check the shift first, then `>40`, then `>10`. Change one thing per iteration.
5. `design_measure` to compare a single card or icon side by side.
6. Finish with the project's own lint / type-check / build gate.

## Requirements

- Node ≥ 22 (the screenshot tool uses the built-in WebSocket).
- Python 3.10+ with Pillow for `design_ir` and `design_measure`; **numpy is additionally required by `design_diff`**.
- A Chromium-based browser installed locally. No browser download, no Playwright.

## Limits

- Text casing in the export can differ from the render; the PNG wins.
- Image content is not in the JSON; only `basic.type == "image"` markers exist. Crop from the PNG or use an average-color placeholder.
- Decoration is only proposed as a candidate; a human or model decides.
- Hidden-layer detection relies on dark pixels inside a text bounding box; it does not detect hidden solid fills.
- Pixel thresholds are per project. Recalibrate instead of copying example values.
