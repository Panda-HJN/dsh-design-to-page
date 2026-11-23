#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""design_measure.py — 设计图量测：裁剪 / 取色 / 平均色 / 设计-实现并排

坐标一律是设计坐标（画板宽），PNG 是 N 倍时脚本自动换算。

  python design_measure.py <design.png> \
    --sample 页面底色:120,240 --avg 卡片图@320,480,400,300 \
    --crop 区块:160,320,800,120:2 --out design-ir/crops

  python design_measure.py <design.png> --shot shot.png \
    --pair 卡片:160,480,800,320 --out design-ir/pairs
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
    base = re.sub(r"[^0-9A-Za-z_\-.\\u4e00-\\u9fff]", "_", base).strip("._")
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
    a = ap.parse_args()

    img = Image.open(a.design).convert("RGB")
    scale = a.scale or (img.width / a.design_width)
    result = {"design": a.design, "png": [img.width, img.height], "scale": round(scale, 4),
              "samples": [], "avgs": [], "files": []}

    def px(x, y):
        return img.getpixel((int(round(x * scale)), int(round(y * scale))))

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

    # 裁剪
    for spec in a.crop:
        name, (x, y, w, h), zoom = parse_box(spec)
        box = (int(x * scale), int(y * scale), int((x + w) * scale), int((y + h) * scale))
        crop = img.crop(box)
        if zoom != 1:
            crop = crop.resize((int(crop.width * zoom), int(crop.height * zoom)), Image.LANCZOS)
        path = os.path.join(a.out, f"crop-{safe_name(name)}.png")
        crop.save(path)
        result["files"].append({"kind": "crop", "name": name, "path": path, "size": list(crop.size)})
        print(f"crop {name} -> {path}")

    # 设计 vs 实现 并排（上设计、下实现、红条分隔）
    if a.shot:
        shot = Image.open(a.shot).convert("RGB")
        sscale = shot.width / a.design_width
        for spec in a.pair:
            name, (x, y, w, h), zoom = parse_box(spec)
            da = img.crop((int(x * scale), int(y * scale), int((x + w) * scale), int((y + h) * scale)))
            db = shot.crop((int(x * sscale), int(y * sscale), int((x + w) * sscale), int((y + h) * sscale)))
            db = db.resize(da.size, Image.LANCZOS)
            if zoom != 1:
                da = da.resize((int(da.width * zoom), int(da.height * zoom)), Image.LANCZOS)
                db = db.resize(da.size, Image.LANCZOS)
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
