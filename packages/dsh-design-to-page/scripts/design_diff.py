#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""design_diff.py — 原生 2x 像素回归：MAE / >10 / >40 / 位移（可遮罩）/ 分区 / JSON 输出

位移搜索用「投影相关性 + 少量全图校验」代替 169 次全图扫描：
169×full-res 在 4K 设计图上要 50s，投影法只要 1s 量级。
"""
import argparse, json, os, sys

try:
    import numpy as np
    from PIL import Image
except ImportError as exc:  # 明确报错，而不是抛一堆栈让调用方误判成"门禁未通过"
    sys.stderr.write(f"design_diff 需要 numpy 与 Pillow（缺少 {exc.name}）：pip install numpy Pillow\n")
    sys.exit(3)

EXIT_GATE = 1
EXIT_USAGE = 2


def load_rgb(path):
    im = Image.open(path).convert("RGB")
    return im


def to_array(im):
    return np.asarray(im, dtype=np.int16)


def _center(arr, frac=0.6):
    h, w = arr.shape[:2]
    ch, cw = max(32, int(h * frac)), max(32, int(w * frac))
    y0, x0 = (h - ch) // 2, (w - cw) // 2
    return arr[y0 : y0 + ch, x0 : x0 + cw]


def _mae(ref, shot, dx, dy, step=1):
    """shot 平移 (dx,dy) 后与 ref 的 MAE；step>1 时按网格抽样，位移仍是整像素"""
    h, w = ref.shape[:2]
    y0s, y0r = (dy, 0) if dy >= 0 else (0, -dy)
    x0s, x0r = (dx, 0) if dx >= 0 else (0, -dx)
    hs, ws = h - abs(dy), w - abs(dx)
    if hs <= 16 or ws <= 16:
        return float("inf")
    a = shot[y0s : y0s + hs : step, x0s : x0s + ws : step]
    b = ref[y0r : y0r + hs : step, x0r : x0r + ws : step]
    return float(np.abs(a - b).mean())


def shift_search(ref, shot, radius, min_gain=0.005):
    """可分离搜索（先 x 后 y）+ 全分辨率定案 + 显著性门槛。

    像素级 MAE 在接近对齐时是"平底"的（本项目实测 (0,0) 6.955 / (1,1) 6.747），
    1px 差异不可靠；因此只有当改进超过 min_gain（默认 0.5%）才报非零位移，
    否则老老实实报 0 —— 避免把噪声当成"版心错了"。
    """
    step = 4
    ref_c, shot_c = _center(ref), _center(shot)

    base = _mae(ref, shot, 0, 0, step)
    best_dx = 0
    for dx in range(-radius, radius + 1):
        c = _mae(ref, shot, dx, 0, step)
        if c < base:
            base, best_dx = c, dx

    best = (best_dx, 0)
    base = _mae(ref, shot, best_dx, 0, step)
    for dy in range(-radius, radius + 1):
        c = _mae(ref, shot, best_dx, dy, step)
        if c < base:
            base, best = c, (best_dx, dy)

    # 全分辨率中心区定案（±1）
    b_c = _mae(ref_c, shot_c, best[0], best[1])
    for dx in range(best[0] - 1, best[0] + 2):
        for dy in range(best[1] - 1, best[1] + 2):
            c = _mae(ref_c, shot_c, dx, dy)
            if c < b_c:
                best, b_c = (dx, dy), c

    zero = _mae(ref_c, shot_c, 0, 0)
    if best != (0, 0) and b_c > zero * (1 - min_gain):
        return 0, 0, zero
    return best[0], best[1], b_c

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", required=True)
    ap.add_argument("--shot", required=True)
    ap.add_argument("--out", default="design-ir")
    ap.add_argument("--design-width", type=int, default=1920)
    ap.add_argument("--mask", action="append", default=[])
    ap.add_argument("--mask-file", default=None)
    ap.add_argument("--region", action="append", default=[])
    ap.add_argument("--shift", type=int, default=6, help="位移搜索半径（设计 px）")
    ap.add_argument("--json", default=None)
    ap.add_argument("--fail-over", type=float, default=None, help=">40 百分比阈值，超过则 exit 1")
    ap.add_argument("--fail-mae", type=float, default=None)
    ap.add_argument("--allow-scale", action="store_true", help="允许非整数倍率（默认拒绝）")
    ap.add_argument("--no-heat", action="store_true")
    a = ap.parse_args()

    # 先删旧指标：避免本次崩溃时把上一次的 metrics.json 当成结果
    out_json = a.json or os.path.join(a.out, "metrics.json")
    if os.path.exists(out_json):
        os.remove(out_json)

    ref_im = load_rgb(a.ref)
    shot_im = load_rgb(a.shot)
    if ref_im.size != shot_im.size:
        print(f"size mismatch ref={ref_im.size} shot={shot_im.size}", file=sys.stderr)
        return EXIT_USAGE

    w, h = ref_im.size
    scale = w / a.design_width
    if abs(scale - round(scale)) > 1e-6 and not a.allow_scale:
        print(
            f"非整数倍率：PNG 宽 {w} / 画板宽 {a.design_width} = {scale:g}。"
            f"请用设计图原始导出（整数倍），或加 --allow-scale 显式放开。",
            file=sys.stderr,
        )
        return EXIT_USAGE
    print(f"canvas {w}x{h} scale={scale:g}")

    ref = to_array(ref_im)
    shot = to_array(shot_im)

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
        if x1 > x0 and y1 > y0:
            mask[y0:y1, x0:x1] = True
    print(f"masks {len(masks)} -> {mask.sum() / (w * h) * 100:.1f}% area")

    def stat(d0, d1, m=None):
        diff = np.abs(d0 - d1).max(axis=2)
        if m is not None:
            diff = np.where(m, 0, diff)
        return float(diff.mean()), float((diff > 10).mean() * 100), float((diff > 40).mean() * 100)

    mae, p10, p40 = stat(ref, shot, mask)
    mae_all, p10_all, p40_all = stat(ref, shot, None)
    print(f"MAE {mae:.2f}  >10 {p10:.2f}%  >40 {p40:.2f}%   (全图 MAE {mae_all:.2f} / >40 {p40_all:.2f}%)")

    def color_variety(arr):
        """量化后统计颜色多样性：空白/错误页几乎是单色"""
        q = arr[::8, ::8].reshape(-1, 3) // 16
        return int(len(np.unique(q, axis=0)))

    variety = color_variety(shot)
    ref_variety = color_variety(ref)

    # 截图有效性：整页几乎全错、或几乎是单色，通常不是"还原得差"，
    # 而是截图不是目标页面（HMR 中途、Vite 错误浮层、路由跳走、服务重启）
    if p40 > 60 or mae > 50 or (variety < 8 and ref_variety > 32):
        sys.stderr.write(
            f"截图不可用：MAE {mae:.1f} / >40 {p40:.1f}% / 颜色数 {variety}（设计图 {ref_variety}）说明这张图不是目标页面"
            "（HMR 未完成、错误浮层、路由跳走或服务重启）。请重新截图后再比。\n"
        )
        sys.stderr.write("本次不产出 metrics.json 与热力图，避免把无效截图当成门禁结果。\n")
        return EXIT_USAGE

    # 位移搜索在原生分辨率上做（精度 = 1 PNG px），抽样提速
    radius_px = max(1, round(a.shift * scale))
    dx, dy, _ = shift_search(ref, shot, radius_px)
    print(f"shift dx={dx} dy={dy} (png px) -> design {dx / scale:+.1f},{dy / scale:+.1f}")

    regions = []
    for reg in a.region:
        name, box = reg.split(":")
        x, y, rw, rh = [float(v) for v in box.split(",")]
        x0, y0, x1, y1 = int(x * scale), int(y * scale), int((x + rw) * scale), int((y + rh) * scale)
        m, q10, q40 = stat(ref[y0:y1, x0:x1], shot[y0:y1, x0:x1],
                           mask[y0:y1, x0:x1] if mask.any() else None)
        regions.append(dict(name=name, mae=round(m, 2), p10=round(q10, 1), p40=round(q40, 1)))
        print(f"  [{name}] MAE {m:.2f}  >10 {q10:.1f}%  >40 {q40:.1f}%")

    heat_path = ""
    if not a.no_heat:
        diff = np.abs(ref - shot).max(axis=2)
        diff = np.where(mask, 0, diff).astype(np.uint8)
        heat = np.zeros((h, w, 3), dtype=np.uint8)
        heat[..., 0] = np.clip(diff * 3, 0, 255)
        heat[..., 1] = np.clip(diff, 0, 255) // 3
        os.makedirs(a.out, exist_ok=True)
        heat_path = os.path.join(a.out, "diff-heat.png")
        Image.fromarray(heat).save(heat_path)

    os.makedirs(os.path.dirname(out_json) or ".", exist_ok=True)
    json.dump(
        dict(
            mae=round(mae, 2), p10=round(p10, 2), p40=round(p40, 2),
            maeAll=round(mae_all, 2), p10All=round(p10_all, 2), p40All=round(p40_all, 2),
            shift=[dx, dy], shiftDesign=[round(dx / scale, 2), round(dy / scale, 2)],
            masks=len(masks), regions=regions, size=[w, h], scale=scale,
        ),
        open(out_json, "w", encoding="utf-8"),
        indent=1,
    )

    if a.fail_over is not None and p40 > a.fail_over:
        print(f"FAIL >40 {p40:.2f}% > {a.fail_over}%")
        return EXIT_GATE
    if a.fail_mae is not None and mae > a.fail_mae:
        print(f"FAIL MAE {mae:.2f} > {a.fail_mae}")
        return EXIT_GATE
    return 0


if __name__ == "__main__":
    sys.exit(main())
