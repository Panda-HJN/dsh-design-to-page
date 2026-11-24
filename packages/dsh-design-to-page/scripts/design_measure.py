#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""design_measure.py — 设计图量测：裁剪 / 取色 / 平均色 / 设计-实现并排

坐标一律是设计坐标（画板宽），PNG 是 N 倍时脚本自动换算。

  python design_measure.py <design.png> \
    --sample 页面底色:60,300 --avg 卡片图@169,322,336,220 \
    --crop 页签:120,205,900,85:2 --out design-ir/crops

  python design_measure.py <design.png> --shot shot.png \
    --pair 卡片:144,369,802,310 --out design-ir/pairs
"""
import argparse
import json
import os
import re
import sys

try:
    from PIL import Image, ImageDraw
except ImportError:
    sys.stderr.write("design_measure 需要 Pillow：pip install Pillow" + "\n")
    sys.exit(3)


def safe_name(name):
    """文件名只保留安全字符：调用方传的名字不可信"""
    base = os.path.basename(name.replace("\\", "/"))
    base = re.sub(r"[^0-9A-Za-z_\-.\u4e00-\u9fff]", "_", base).strip("._")
    return base or "item"


def parse_box(s):
    """name:x,y,w,h[:zoom]  → (name, box, zoom)"""
    name, _, rest = s.partition(":")
    parts = rest.split(":")
    nums = [float(v) for v in parts[0].split(",")]
    zoom = float(parts[1]) if len(parts) > 1 else 1.0
    return name, nums, zoom


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("design")
    ap.add_argument("--shot", default=None)
    ap.add_argument("--crop", action="append", default=[])
    ap.add_argument("--pair", action="append", default=[])
    ap.add_argument("--sample", action="append", default=[])
    ap.add_argument("--avg", action="append", default=[])
    ap.add_argument("--out", default="design-ir/measure")
    ap.add_argument("--design-width", type=int, default=1920)
    ap.add_argument("--scale", type=float, default=None)
    ap.add_argument("--resample", choices=["nearest", "lanczos"], default="nearest", help="放大采样：nearest 看 1px 更准（默认），lanczos 更平滑")
    a = ap.parse_args()

    img = Image.open(a.design).convert("RGB")
    scale = a.scale or (img.width / a.design_width)
    result = {"design": a.design, "png": [img.width, img.height], "scale": round(scale, 4),
              "samples": [], "avgs": [], "files": []}

    def px(x, y):
        # 越界点直接夹到边界，避免取色时抛 IndexError 或拿到黑边
        ix = min(max(0, int(round(x * scale))), img.width - 1)
        iy = min(max(0, int(round(y * scale))), img.height - 1)
        return img.getpixel((ix, iy))

    def region_avg(x, y, w, h):
        box = (int(x * scale), int(y * scale), int((x + w) * scale), int((y + h) * scale))
        crop = img.crop(box)
        n = crop.width * crop.height
        if n == 0:
            return None
        data = crop.load()
        r = g = b = 0
        for yy in range(crop.height):
            for xx in range(crop.width):
                p = data[xx, yy]
                r += p[0]; g += p[1]; b += p[2]
        return (round(r / n), round(g / n), round(b / n))

    def hexs(rgb):
        return "#%02X%02X%02X" % rgb

    # 取色：3x3 邻域平均，避开单像素抗锯齿
    for spec in a.sample:
        name, coords = spec.split(":")
        x, y = [float(v) for v in coords.split(",")]
        pts = [px(x + dx, y + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)]
        rgb = tuple(round(sum(p[i] for p in pts) / len(pts)) for i in range(3))
        result["samples"].append({"name": name, "at": [x, y], "rgb": list(rgb), "hex": hexs(rgb)})
        print(f"sample {name} @({x},{y}) = {hexs(rgb)}")

    # 区域平均色（图片占位色块用）
    for spec in a.avg:
        name, _, box = spec.partition("@")
        x, y, w, h = [float(v) for v in box.split(",")]
        rgb = region_avg(x, y, w, h)
        if rgb:
            result["avgs"].append({"name": name, "box": [x, y, w, h], "rgb": list(rgb), "hex": hexs(rgb)})
            print(f"avg {name} = {hexs(rgb)}")

    os.makedirs(a.out, exist_ok=True)
    resample = Image.NEAREST if a.resample == "nearest" else Image.LANCZOS

    # 裁剪
    for spec in a.crop:
        name, (x, y, w, h), zoom = parse_box(spec)
        box = (int(x * scale), int(y * scale), int((x + w) * scale), int((y + h) * scale))
        crop = img.crop(box)
        if zoom != 1:
            crop = crop.resize((int(crop.width * zoom), int(crop.height * zoom)), resample)
        path = os.path.join(a.out, f"crop-{safe_name(name)}.png")
        crop.save(path)
        result["files"].append({"kind": "crop", "name": name, "path": path, "size": list(crop.size)})
        print(f"crop {name} -> {path}")

    # 设计 vs 实现 并排（上设计、下实现、红条分隔）
    if a.shot:
        shot = Image.open(a.shot).convert("RGB")
        sscale = shot.width / a.design_width
        if abs(sscale - scale) / scale > 0.01:
            sys.stderr.write(
                f"--pair 要求两张图同倍率：设计 {scale:g}x、截图 {sscale:g}x。"
                f"截图请按画板宽 × 设计倍率输出（design_shot --width {a.design_width} --dpr {scale:g}）。\n"
            )
            return 2
        for spec in a.pair:
            name, (x, y, w, h), zoom = parse_box(spec)
            da = img.crop((int(x * scale), int(y * scale), int((x + w) * scale), int((y + h) * scale)))
            db = shot.crop((int(x * sscale), int(y * sscale), int((x + w) * sscale), int((y + h) * sscale)))
            if db.size != da.size:
                db = db.resize(da.size, Image.BOX)
            if zoom != 1:
                da = da.resize((int(da.width * zoom), int(da.height * zoom)), resample)
                db = db.resize(da.size, resample)
            canvas = Image.new("RGB", (da.width, da.height * 2 + 6), (220, 40, 40))
            canvas.paste(da, (0, 0))
            canvas.paste(db, (0, da.height + 6))
            path = os.path.join(a.out, f"pair-{safe_name(name)}.png")
            canvas.save(path)
            result["files"].append({"kind": "pair", "name": name, "path": path, "size": list(canvas.size)})
            print(f"pair {name} -> {path}")

    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
