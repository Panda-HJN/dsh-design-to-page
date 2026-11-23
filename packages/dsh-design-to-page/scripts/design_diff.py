#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""design_diff.py — 原生 2x 像素回归：MAE / >10 / >40 / 位移（可遮罩）/ 分区 / JSON 输出"""
import argparse, json, os, sys

try:
    import numpy as np
    from PIL import Image
except ImportError as exc:  # 明确报错，而不是抛一堆栈让调用方误判成"门禁未通过"
    sys.stderr.write(f"design_diff 需要 numpy 与 Pillow（缺少 {exc.name}）：pip install numpy Pillow\n")
    sys.exit(3)


def load(path):
    im = Image.open(path).convert("RGB")
    return np.asarray(im, dtype=np.int16)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", required=True)
    ap.add_argument("--shot", required=True)
    ap.add_argument("--out", default="design-ir")
    ap.add_argument("--design-width", type=int, default=1920)
    ap.add_argument("--mask", action="append", default=[])
    ap.add_argument("--mask-file", default=None)
    ap.add_argument("--region", action="append", default=[])
    ap.add_argument("--shift", type=int, default=6)
    ap.add_argument("--json", default=None)
    ap.add_argument("--fail-over", type=float, default=None, help=">40 百分比阈值，超过则 exit 1")
    ap.add_argument("--fail-mae", type=float, default=None)
    ap.add_argument("--no-heat", action="store_true")
    a = ap.parse_args()

    # 先删旧指标：避免本次崩溃时把上一次的 metrics.json 当成结果
    out_json = a.json or os.path.join(a.out, "metrics.json")
    if os.path.exists(out_json):
        os.remove(out_json)

    ref = load(a.ref)
    shot = load(a.shot)
    if ref.shape != shot.shape:
        print(f"size mismatch ref={ref.shape} shot={shot.shape}", file=sys.stderr)
        return 2
    h, w = ref.shape[:2]
    scale = w / a.design_width
    print(f"canvas {w}x{h} scale={scale:g}")

    # masks: design coords -> png coords
    masks = []
    for m in a.mask:
        x, y, mw, mh = [float(v) for v in m.split(",")]
        masks.append((x, y, mw, mh))
    if a.mask_file and os.path.exists(a.mask_file):
        data = json.load(open(a.mask_file, encoding="utf-8"))
        for s in data.get("slots", []):
            masks.append((s["x"], s["y"], s["w"], s["h"]))
    mask = np.zeros((h, w), dtype=bool)
    for x, y, mw, mh in masks:
        x0, y0 = max(0, int(round(x * scale))), max(0, int(round(y * scale)))
        x1, y1 = min(w, int(round((x + mw) * scale))), min(h, int(round((y + mh) * scale)))
        mask[y0:y1, x0:x1] = True
    print(f"masks {len(masks)} -> {mask.sum() / (w * h) * 100:.1f}% area")

    def stat(d0, d1, m=None):
        diff = np.abs(d0 - d1).max(axis=2)
        if m is not None:
            diff = np.where(m, 0, diff)
        return float(diff.mean()), float((diff > 10).mean() * 100), float((diff > 40).mean() * 100)

    mae, p10, p40 = stat(ref, shot, mask)
    print(f"MAE {mae:.2f}  >10 {p10:.2f}%  >40 {p40:.2f}%")

    # shift search on masked
    best = (0, 0, mae)
    r = a.shift
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            s0 = shot[max(0, dy):h + min(0, dy), max(0, dx):w + min(0, dx)]
            r0 = ref[max(0, -dy):h + min(0, -dy), max(0, -dx):w + min(0, -dx)]
            mm = mask[max(0, -dy):h + min(0, -dy), max(0, -dx):w + min(0, -dx)]
            m, _, _ = stat(r0, s0, mm)
            if m < best[2]:
                best = (dx, dy, m)
    print(f"shift dx={best[0]} dy={best[1]} (png px) -> design {best[0]/scale:+.1f},{best[1]/scale:+.1f}  MAE {best[2]:.2f}")

    regions = []
    for reg in a.region:
        name, box = reg.split(":")
        x, y, rw, rh = [float(v) for v in box.split(",")]
        x0, y0, x1, y1 = int(x * scale), int(y * scale), int((x + rw) * scale), int((y + rh) * scale)
        m, q10, q40 = stat(ref[y0:y1, x0:x1], shot[y0:y1, x0:x1],
                           mask[y0:y1, x0:x1] if mask.any() else None)
        regions.append(dict(name=name, mae=round(m, 2), p10=round(q10, 1), p40=round(q40, 1)))
        print(f"  [{name}] MAE {m:.2f}  >10 {q10:.1f}%  >40 {q40:.1f}%")

    if not a.no_heat:
        diff = np.abs(ref - shot).max(axis=2)
        diff = np.where(mask, 0, diff).astype(np.uint8)
        heat = np.zeros((h, w, 3), dtype=np.uint8)
        heat[..., 0] = np.clip(diff * 3, 0, 255)
        heat[..., 1] = np.clip(diff, 0, 255) // 3
        os.makedirs(a.out, exist_ok=True)
        Image.fromarray(heat).save(os.path.join(a.out, "diff-heat.png"))

    out_json = a.json or os.path.join(a.out, "metrics.json")
    os.makedirs(os.path.dirname(out_json), exist_ok=True)
    json.dump(dict(mae=round(mae, 2), p10=round(p10, 2), p40=round(p40, 2), shift=[best[0], best[1]],
                   masks=len(masks), regions=regions, size=[w, h], scale=scale),
              open(out_json, "w", encoding="utf-8"), indent=1)

    if a.fail_over is not None and p40 > a.fail_over:
        print(f"FAIL >40 {p40:.2f}% > {a.fail_over}%")
        return 1
    if a.fail_mae is not None and mae > a.fail_mae:
        print(f"FAIL MAE {mae:.2f} > {a.fail_mae}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
