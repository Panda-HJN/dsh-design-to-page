# -*- coding: utf-8 -*-
"""fixtures：负宽高 / 渐变 / 对象根 / 中文文件名 —— 四类回归都在这一个脚本里

  python tests/fixtures_check.py

不依赖外部数据，自己造最小标注 JSON。
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
SCRIPTS = os.path.join(PKG, "scripts")
PY = sys.executable


def run(args, expect=0):
    r = subprocess.run([PY] + args, capture_output=True, text=True, encoding="utf-8")
    if r.returncode != expect:
        print(r.stdout)
        print(r.stderr)
        raise SystemExit(f"exit {r.returncode} != {expect}: {' '.join(args[:3])}")
    return r


def fixture(root_is_list=True):
    mirrored = {
        "basic": {"id": "n1", "name": "Mirrored", "realType": "FRAME", "type": "shape"},
        "bounds": {"left": 100, "top": 100, "width": -50, "height": 40},
        "fill": {"colors": []},
        "stroke": {"borders": [], "radius": []},
        "text": {},
        "children": [],
    }
    # 贴着画板外沿、但子树里有文本 —— 不能算装饰（旧实现的典型误报）
    text_box = {
        "basic": {"id": "n3", "name": "HeaderLike", "realType": "FRAME", "type": "shape"},
        "bounds": {"left": 0, "top": 10, "width": 300, "height": 60},
        "fill": {"colors": []},
        "stroke": {"borders": [], "radius": []},
        "text": {},
        "children": [
            {
                "basic": {"id": "n3t", "name": "Title", "realType": "TEXT", "type": "text"},
                "bounds": {"left": 8, "top": 18, "width": 120, "height": 20},
                "text": {"styles": [{"value": "Title", "font": {"family": "Inter", "weight": "Regular", "size": 14,
                                 "color": {"type": "normal", "value": {"r": 0, "g": 0, "b": 0, "a": 1}}},
                                 "space": {"lineHeight": 20}}]},
                "fill": {"colors": []},
                "stroke": {"borders": [], "radius": []},
                "children": [],
            }
        ],
    }
    # 真装饰：贴边、无文本、icon-like、半透明
    decor = {
        "basic": {"id": "n4", "name": "DecorIcon", "realType": "FRAME", "type": "shape", "opacity": 0.6},
        "bounds": {"left": 0, "top": 700, "width": 90, "height": 90},
        "fill": {"colors": []},
        "stroke": {"borders": [], "radius": []},
        "text": {},
        "children": [
            {
                "basic": {"id": "n4v", "name": "Vector", "realType": "VECTOR", "type": "shape"},
                "bounds": {"left": 5, "top": 705, "width": 80, "height": 80},
                "fill": {"colors": [{"type": "normal", "value": {"r": 200, "g": 200, "b": 200, "a": 1}}]},
                "stroke": {"borders": [], "radius": []},
                "text": {},
                "children": [],
            }
        ],
    }
    gradient = {
        "basic": {"id": "n2", "name": "Gradient", "realType": "FRAME", "type": "shape"},
        "bounds": {"left": 0, "top": 0, "width": 300, "height": 200},
        "fill": {
            "colors": [
                {"type": "normal", "value": {"r": 255, "g": 255, "b": 255, "a": 1}},
                {
                    "type": "linearGradient",
                    "value": {
                        "fromX": 0,
                        "fromY": 0,
                        "toX": 300,
                        "toY": 200,
                        "colorStops": [
                            {"color": {"r": 0, "g": 0, "b": 0, "a": 1}, "position": 0},
                            {"color": {"r": 255, "g": 255, "b": 255, "a": 1}, "position": 1},
                        ],
                    },
                },
            ]
        },
        "stroke": {"borders": [], "radius": []},
        "text": {},
        "children": [mirrored],
    }
    root = {
        "basic": {"id": "root", "name": "Board", "realType": "FRAME", "type": "shape"},
        "bounds": {"left": 0, "top": 0, "width": 375, "height": 800},
        "fill": {"colors": []},
        "stroke": {"borders": [], "radius": []},
        "text": {},
        "children": [gradient, text_box, decor],
    }
    return [root] if root_is_list else root


def main():
    tmp = tempfile.mkdtemp(prefix="d2p-fixture-")
    ok = True

    # 1) 列表根 + 负宽高 + 渐变
    src = os.path.join(tmp, "list.json")
    json.dump(fixture(True), open(src, "w", encoding="utf-8"))
    out = os.path.join(tmp, "ir-list")
    run([os.path.join(SCRIPTS, "design_ir.py"), src, "--out", out])
    ir = json.load(open(os.path.join(out, "ir.json"), encoding="utf-8"))

    def find(node, name):
        if node["name"] == name:
            return node
        for c in node["children"]:
            hit = find(c, name)
            if hit:
                return hit
        return None

    m = find(ir, "Mirrored")
    g = find(ir, "Gradient")
    checks = [
        ("负宽高转正", m["w"] == 50 and m["x"] == 50),
        ("mirrored 标记", m["mirrored"] is True),
        ("渐变保留 stops", any(f["kind"] == "linearGradient" and len(f["stops"]) == 2 for f in g["fills"])),
        # 375 宽画板不能再用 120 版心
        ("移动端版心", json.load(open(os.path.join(out, "invariants.json"), encoding="utf-8"))["boardGutter"] < 60),
    ]

    deco = [s for s in json.load(open(os.path.join(out, "masks.json"), encoding="utf-8"))["slots"]
            if s["kind"] == "decoration"]
    checks.append(("装饰只认真装饰（含文本的容器不算）", len(deco) == 1 and abs(deco[0]["w"] - 90) < 1))

    # 策略 background：装饰不再进遮罩
    out_bg = os.path.join(tmp, "ir-bg")
    run([os.path.join(SCRIPTS, "design_ir.py"), src, "--out", out_bg, "--decor", "background"])
    deco_bg = [s for s in json.load(open(os.path.join(out_bg, "masks.json"), encoding="utf-8"))["slots"]
               if s["kind"] == "decoration"]
    checks.append(("decor=background 时不遮装饰", len(deco_bg) == 0))
    for label, passed in checks:
        print(("PASS " if passed else "FAIL ") + label)
        ok = ok and passed

    # 2) 对象根
    src2 = os.path.join(tmp, "object.json")
    json.dump(fixture(False), open(src2, "w", encoding="utf-8"))
    out2 = os.path.join(tmp, "ir-obj")
    run([os.path.join(SCRIPTS, "design_ir.py"), src2, "--out", out2])
    ir2 = json.load(open(os.path.join(out2, "ir.json"), encoding="utf-8"))
    passed = ir2["name"] == "Board"
    print(("PASS " if passed else "FAIL ") + "对象根节点")
    ok = ok and passed

    # 3) 中文 crop 名不塌缩
    from PIL import Image

    png = os.path.join(tmp, "tiny.png")
    Image.new("RGB", (100, 100), (10, 200, 30)).save(png)
    out3 = os.path.join(tmp, "m")
    run([os.path.join(SCRIPTS, "design_measure.py"), png, "--design-width", "100",
         "--crop", "页头:0,0,20,20", "--crop", "卡片:20,20,20,20", "--out", out3])
    files = sorted(os.listdir(out3))
    passed = len([f for f in files if f.startswith("crop-")]) == 2 and any("页头" in f for f in files)
    print(("PASS " if passed else "FAIL ") + f"中文文件名（{files}）")
    ok = ok and passed

    print("\\n" + ("全部通过" if ok else "有失败项"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
