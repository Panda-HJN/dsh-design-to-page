#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
design_ir.py — 设计标注 JSON → IR（中间表示）

定位：把"绝对坐标画板"翻译成"布局文法 + 结构语义 + 资产/遮罩线索"，
     供人或 agent 复核后写页面。它不生成页面代码。

输出（默认 design-ir/）：
  ir.json        规整后的树：视觉序 + z 序 + 每容器布局假设(证据/置信/备选)
  layout.json    仅布局假设（便于 agent 只读这一份）
  tree.txt       视觉序缩进树（人看）
  report.md      概览 / 文本样式表 / 色板 / 布局指纹 / 组件聚类 / 图片槽 / 装饰 / 脏数据
  masks.json     图片槽 + 装饰候选（给 design_diff.py --mask-file 用）
  semantics.json 结构语义：分割线 / 图标簇 / 文本缩进 / 空容器

用法：
  python design_ir.py <layerData.json> --out design-ir [--board 1920] [--gutter 120] [--tol 2]
"""

import argparse
import json
import os
import sys
from collections import Counter, defaultdict

# ---------------------------------------------------------------- 数据规整


def _c(color):
    """颜色对象 → (r,g,b,a) 整数/浮点，兼容 {value:{...}} 与直接 {r,g,b,a}"""
    if not isinstance(color, dict):
        return None
    v = color.get("value", color)
    if "r" not in v:
        return None
    return (round(v["r"]), round(v["g"]), round(v["b"]), round(v.get("a", 1), 3))


def _hex(c):
    return "#%02X%02X%02X" % c[:3] if c else None


def _borders(n):
    out = []
    for side in (n.get("stroke") or {}).get("borders") or []:
        segs = side if isinstance(side, list) else [side]
        for s in segs:
            if isinstance(s, dict) and "strokeWidth" in s:
                out.append(
                    {
                        "side": s.get("direction") or "inside",
                        "width": s.get("strokeWidth"),
                        "type": s.get("type") or "inside",
                        "color": _c(s.get("color")) and _hex(_c(s.get("color"))),
                    }
                )
    return out


def _radius(n):
    r = (n.get("stroke") or {}).get("radius") or []
    if not r:
        return None
    # 导出器对"完全圆角/胶囊"用了超大哨兵值（>1000），归一成 pill
    if any(v > 1000 for v in r):
        return "pill"
    b = n["bounds"]
    half = min(b.get("width", 0), b.get("height", 0)) / 2
    vals = [round(min(float(v), half), 1) for v in r]
    return vals[0] if len(set(vals)) == 1 else vals


def _texts(n):
    out = []
    for st in (n.get("text") or {}).get("styles") or []:
        f = st.get("font") or {}
        sp = st.get("space") or {}
        lh = sp.get("lineHeight")
        out.append(
            {
                "value": st.get("value", ""),
                "size": f.get("size"),
                "lineHeight": round(lh, 1) if lh else f.get("size"),
                "lineHeightInt": round(lh) if lh else f.get("size"),
                "weight": f.get("weight"),
                "family": f.get("family"),
                "color": _hex(_c(f.get("color"))),
                "align": st.get("align"),
                "letterSpacing": sp.get("letterSpacing", 0),
            }
        )
    return out


def normalize(raw):
    """raw[0] 是完整嵌套树，其余是平铺副本"""

    def walk(n, parent=None, z=0):
        b = n.get("bounds") or {}
        node = {
            "id": (n.get("basic") or {}).get("id"),
            "name": (n.get("basic") or {}).get("name", ""),
            "kind": (n.get("basic") or {}).get("realType", "UNKNOWN"),
            "semantic": (n.get("basic") or {}).get("type"),
            "opacity": (n.get("basic") or {}).get("opacity", 1),
            "x": round(b.get("left", 0), 2),
            "y": round(b.get("top", 0), 2),
            "w": round(b.get("width", 0), 2),
            "h": round(b.get("height", 0), 2),
            "radius": _radius(n),
            "fill": [_hex(_c(c)) for c in (n.get("fill") or {}).get("colors") or []],
            "borders": _borders(n),
            "shadows": [
                {
                    "x": s.get("offsetX", 0),
                    "y": s.get("offsetY", 0),
                    "blur": s.get("blur", 0),
                    "spread": s.get("spread", 0),
                    "color": _c(s.get("color")),
                }
                for s in (n.get("effect") or {}).get("shadows") or []
            ],
            "texts": _texts(n),
            "z": z,
            "pid": parent["id"] if parent else None,
            "children": [],
        }
        for i, c in enumerate(n.get("children") or []):
            node["children"].append(walk(c, node, i))
        return node

    root = walk(raw[0])
    return root


# ---------------------------------------------------------------- 布局推断

TOL = 2.0


def _cluster(items, axis, tol):
    """按 axis（'x' 或 'y'）聚类，返回 [[item,...], ...]（按坐标升序）"""
    if not items:
        return []
    key = lambda n: n[axis]
    ordered = sorted(items, key=key)
    groups, cur = [], [ordered[0]]
    for it in ordered[1:]:
        if abs(key(it) - key(cur[0])) <= tol:
            cur.append(it)
        else:
            groups.append(cur)
            cur = [it]
    groups.append(cur)
    return groups


def _cluster_overlap(items, start_key, size_key, tol):
    """按"区间重叠"聚类：不同字号的同一行文字 y 不同但垂直重叠，应算同一行"""
    if not items:
        return []
    ordered = sorted(items, key=lambda n: n[start_key])
    groups, cur = [], [ordered[0]]
    cur_end = ordered[0][start_key] + ordered[0][size_key]
    for it in ordered[1:]:
        s = it[start_key]
        if s < cur_end - tol:
            cur.append(it)
            cur_end = max(cur_end, s + it[size_key])
        else:
            groups.append(cur)
            cur = [it]
            cur_end = s + it[size_key]
    groups.append(cur)
    return groups


def _gaps(groups, axis, size_key):
    """相邻两组之间的间隙（不跨行/列配对）"""
    out = []
    for a, b in zip(groups, groups[1:]):
        a_max = max(n[axis] + n[size_key] for n in a)
        b_min = min(n[axis] for n in b)
        out.append(round(b_min - a_max, 1))
    return out


def _uniform(vals, eps=0.6):
    return bool(vals) and max(vals) - min(vals) <= eps


def infer_layout(node, tol):
    kids = [c for c in node["children"] if c["w"] > 0 and c["h"] > 0]
    if not kids:
        return {"kind": "leaf"}

    # 相对坐标
    rel = []
    for c in kids:
        d = dict(c)
        d["rx"] = round(c["x"] - node["x"], 1)
        d["ry"] = round(c["y"] - node["y"], 1)
        rel.append(d)

    x0 = min(c["rx"] for c in rel)
    y0 = min(c["ry"] for c in rel)
    x1 = max(c["rx"] + c["w"] for c in rel)
    y1 = max(c["ry"] + c["h"] for c in rel)
    border = max([b["width"] for b in node["borders"]] or [0])
    pad = {
        "top": round(y0 - border, 1),
        "left": round(x0 - border, 1),
        "right": round(node["w"] - x1 - border, 1),
        "bottom": round(node["h"] - y1 - border, 1),
    }
    content_w = round(node["w"] - 2 * border - pad["left"] - pad["right"], 1)

    if len(rel) == 1:
        c = rel[0]
        return {
            "kind": "stack",
            "children": 1,
            "padding": pad,
            "evidence": [f"单子节点 @({c['rx']},{c['ry']}) {c['w']}x{c['h']}"],
            "confidence": 1.0 if c["rx"] - border == pad["left"] else 0.7,
        }

    # 重叠 → 画布叠放（视觉稿里图标零件、背景装饰都是这种）
    union_area = (x1 - x0) * (y1 - y0)
    sum_area = sum(c["w"] * c["h"] for c in rel)
    overlap = sum_area / union_area if union_area > 0 else 1
    if overlap > 1.28:
        return {
            "kind": "overlay",
            "children": len(rel),
            "padding": pad,
            "evidence": [f"{len(rel)} 个子节点互相重叠（面积比 {overlap:.2f}）→ 叠放而非文档流"],
            "confidence": 0.9,
            "positions": [{"x": c["rx"], "y": c["ry"], "w": c["w"], "h": c["h"]} for c in rel],
        }

    rows = _cluster_overlap(rel, "ry", "h", tol)
    row_cells = [_cluster_overlap(row, "rx", "w", tol) for row in rows]
    cell_counts = [len(r) for r in row_cells]
    nrow, ncol = len(rows), max(cell_counts)

    def flat_cells():
        return [c for rowgroups in row_cells for group in rowgroups for c in group]

    def cell_ok():
        cells = flat_cells()
        ws = [round(c["w"], 1) for c in cells]
        hs = [round(c["h"], 1) for c in cells]
        return _uniform(ws) and _uniform(hs)

    def gaps_ok():
        gx, gy = [], []
        for r in row_cells:
            gx += _gaps(r, "rx", "w")
        gy += _gaps(rows, "ry", "h")
        return (not gx or _uniform(gx)) and (not gy or _uniform(gy)), (gx[0] if gx else 0), (gy[0] if gy else 0)

    if nrow >= 1 and ncol >= 2 and cell_ok():
        flat = flat_cells()
        g_ok, gap_x, gap_y = gaps_ok()
        cell = flat[0]
        div_ok = abs(content_w - (ncol * cell["w"] + gap_x * (ncol - 1))) < 1.0
        conf = 1.0
        ev = [
            f"{nrow}×{ncol} grid",
            f"单元格 {cell['w']}×{cell['h']}",
            f"gapX={gap_x}" + (f" gapY={gap_y}" if nrow > 1 else ""),
            f"内容宽 {content_w} = {ncol}×{cell['w']} + {ncol - 1}×{gap_x}" + ("" if div_ok else "（不整除，需复核）"),
            f"padding T{pad['top']} R{pad['right']} B{pad['bottom']} L{pad['left']}",
        ]
        if not g_ok:
            conf -= 0.2
            ev.append("间距不均匀")
        if not div_ok:
            conf -= 0.25
        if len(set(cell_counts)) > 1:
            conf -= 0.2
            ev.append(f"各行列数不一致 {cell_counts}")
        return {
            "kind": "grid",
            "cols": ncol,
            "rows": nrow,
            "gapX": gap_x,
            "gapY": gap_y,
            "cell": {"w": cell["w"], "h": cell["h"]},
            "padding": pad,
            "contentWidth": content_w,
            "evidence": ev,
            "confidence": round(conf, 2),
        }

    if nrow >= 2 and ncol == 1 and _uniform([round(c["rx"], 1) for c in rel]):
        g = _gaps(rows, "ry", "h")
        if _uniform(g):
            return {
                "kind": "flex-col",
                "gap": g[0],
                "padding": pad,
                "evidence": [f"{nrow} 行纵向堆叠", f"gapY={g[0]}", "x 对齐"],
                "confidence": 0.95,
            }

    if nrow == 1 and ncol >= 2:
        g = _gaps(row_cells[0], "rx", "w")
        if _uniform(g):
            return {
                "kind": "flex-row",
                "gap": g[0],
                "padding": pad,
                "evidence": [f"{ncol} 列横排", f"gapX={g[0]}", "高度不一致" if not _uniform([c['h'] for c in rel]) else "等高"],
                "confidence": 0.9 if cell_ok() else 0.6,
            }
        flush_l = min(c["rx"] for c in rel) <= border + 1
        flush_r = max(c["rx"] + c["w"] for c in rel) >= node["w"] - border - 1
        if flush_l and flush_r:
            return {
                "kind": "flex-row",
                "justify": "space-between",
                "gap": "auto",
                "padding": pad,
                "evidence": [f"{ncol} 组横排、左右贴边", f"间距不均 {g} → 两端对齐"],
                "confidence": 0.75,
            }

    return {
        "kind": "absolute",
        "padding": pad,
        "evidence": [
            f"无法归入单一文法：rows={nrow} cols={ncol} cells={len(rel)}",
            f"gapX={_gaps(row_cells[0], 'rx', 'w')} 各行列数={cell_counts}",
        ],
        "confidence": 0.35,
        "positions": [{"x": c["rx"], "y": c["ry"], "w": c["w"], "h": c["h"]} for c in rel],
    }


def _uniform_size_ok(rel, nrow, ncol):
    ws = _uniform([round(c["w"], 1) for c in rel])
    hs = _uniform([round(c["h"], 1) for c in rel])
    return ws and hs


# ---------------------------------------------------------------- 结构语义


def collect(node, out, depth=0):
    out.append((depth, node))
    for c in node["children"]:
        collect(c, out, depth + 1)
    return out


def visual_sort(node):
    node["children"].sort(key=lambda c: (round(c["y"]), round(c["x"])))
    for c in node["children"]:
        visual_sort(c)


def is_separator(n):
    return (
        not n["children"]
        and not n["texts"]
        and (n["h"] <= 2 or n["w"] <= 2)
        and (n["fill"] or n["borders"])
        and max(n["w"], n["h"]) >= 16
    )


def is_icon_cluster(n):
    if not n["children"] or n["texts"]:
        return False
    if n["w"] > 48 or n["h"] > 48:
        return False
    return all(c["kind"] in ("VECTOR", "BOOLEAN_OPERATION", "INSTANCE", "FRAME") for c in n["children"])


def signature(n):
    """结构签名：用于同构组件聚类（symbolId 每个实例都不同，不可用）"""
    kinds = tuple(c["kind"] for c in n["children"])
    return (
        n["kind"],
        round(n["w"]),
        round(n["h"]),
        len(n["children"]),
        kinds,
        len(n["texts"]),
        len([c for c in n["children"] if c["texts"]]),
    )


def visibility_check(nodes, png_path, scale):
    """对每个文本节点在 PNG 上采样它的 bbox：一个暗像素都没有 → 该层没有渲染（隐藏图层）"""
    try:
        from PIL import Image
    except ImportError:
        raise SystemExit("design_ir 的 --png 需要 Pillow：pip install Pillow")

    im = Image.open(png_path).convert("RGB")
    w, h = im.size
    lums = []
    step = max(1, w // 400)
    for x in range(0, w, step):
        for y in (2, h - 3):
            p = im.getpixel((x, y))
            lums.append(0.299 * p[0] + 0.587 * p[1] + 0.114 * p[2])
    lums.sort()
    bg = lums[len(lums) // 2] if lums else 255

    out = []
    for _, n in nodes:
        if not n["texts"]:
            continue
        x0 = max(0, int(n["x"] * scale))
        y0 = max(0, int(n["y"] * scale))
        x1 = min(w, int((n["x"] + n["w"]) * scale))
        y1 = min(h, int((n["y"] + n["h"]) * scale))
        if x1 <= x0 or y1 <= y0:
            continue
        crop = im.crop((x0, y0, x1, y1))
        cw, ch = crop.size
        px = crop.load()
        dark = 0
        for yy in range(ch):
            for xx in range(cw):
                p = px[xx, yy]
                if (0.299 * p[0] + 0.587 * p[1] + 0.114 * p[2]) < bg - 25:
                    dark += 1
        ratio = dark / (cw * ch)
        out.append(
            {
                "id": n["id"],
                "text": n["texts"][0]["value"][:40],
                "box": [n["x"], n["y"], n["w"], n["h"]],
                "darkRatio": round(ratio, 4),
                "rendered": ratio > 0.01,
            }
        )
    return out


def analyze(root, board_w, gutter, tol):
    visual_sort(root)
    nodes = collect(root, [])
    for _, n in nodes:
        n["layout"] = infer_layout(n, tol)

    # 父容器内边距 = 该节点相对父内容区的偏移。写页面时"版心 → 行容器 → 卡片"
    # 的那一段 inset 就在这里；缺了它会把卡片撑宽（实测踩过两次）。
    by_id = {n["id"]: n for _, n in nodes}
    for _, n in nodes:
        p = by_id.get(n.get("pid"))
        if p and p["layout"].get("padding"):
            pad = p["layout"]["padding"]
            n["layout"]["outer"] = pad
            if any(abs(pad[k]) > 0.5 for k in ("left", "right", "top", "bottom")):
                n["layout"].setdefault("evidence", []).append(
                    f"父容器内边距 T{pad['top']} R{pad['right']} B{pad['bottom']} L{pad['left']}"
                )

    # 组件聚类
    buckets = defaultdict(list)
    for _, n in nodes:
        if n["children"] and n["w"] * n["h"] > 2000:
            buckets[signature(n)].append(n)
    components = []
    for sig, group in buckets.items():
        if len(group) < 2:
            continue
        sample = group[0]
        components.append(
            {
                "signature": {
                    "kind": sig[0],
                    "size": f"{sig[1]}×{sig[2]}",
                    "children": sig[4],
                    "texts": sig[5],
                },
                "count": len(group),
                "size": f"{sig[1]}×{sig[2]}",
                "exampleName": sample["name"],
                "exampleId": sample["id"],
                "textSample": [t["value"] for t in sample["texts"]][:2],
                "layout": sample["layout"]["kind"],
            }
        )
    components.sort(key=lambda c: -c["count"])
    groups = {
        f"{g[0]['name']}#{g[0]['w']:.0f}x{g[0]['h']:.0f}": g
        for g in buckets.values()
        if len(g) >= 2 and g[0]["w"] * g[0]["h"] > 2000
    }

    # 图片槽 / 装饰 / 分割线 / 图标簇
    images, separators, icons, empty = [], [], [], []
    for depth, n in nodes:
        if n.get("semantic") == "image":
            images.append(n)
        if is_separator(n):
            separators.append(n)
        if is_icon_cluster(n):
            icons.append(n)
        if not n["children"] and not n["texts"] and not n["fill"] and not n["borders"]:
            empty.append(n)

    # 装饰候选：贴着画板外沿 / 只含图标且无文本 / 低透明度
    deco = []
    for depth, n in nodes:
        if n["w"] < 8 or n["h"] < 8:
            continue
        edge = n["x"] < gutter * 0.6 or n["x"] + n["w"] > board_w - gutter * 0.6
        if n["children"] and not n["texts"] and edge and n["w"] * n["h"] > 4000:
            deco.append((n, "贴画板外沿且无文本"))
        elif 0 < n["opacity"] < 1 and n["w"] * n["h"] > 4000:
            deco.append((n, "低透明度"))

    return {
        "nodes": nodes,
        "components": components,
        "groups": groups,
        "images": images,
        "separators": separators,
        "icons": icons,
        "empty": empty,
        "decorations": deco,
    }


# ---------------------------------------------------------------- 报告


def style_table(nodes):
    t = Counter()
    for _, n in nodes:
        for tx in n["texts"]:
            t[(tx["family"], tx["weight"], tx["size"], tx["lineHeight"], tx["lineHeightInt"], tx["color"], tx["align"])] += 1
    return t.most_common()


def palette(nodes):
    fills, texts, borders = Counter(), Counter(), Counter()
    for _, n in nodes:
        for f in n["fill"]:
            fills[f] += 1
        for tx in n["texts"]:
            texts[tx["color"]] += 1
        for b in n["borders"]:
            borders[(b["color"], b["side"], b["width"])] += 1
    return fills.most_common(), texts.most_common(), borders.most_common()


def tree_lines(root):
    lines = []

    def rec(n, depth):
        lay = n["layout"]
        tag = lay["kind"]
        if tag == "grid":
            tag = f"grid {lay['rows']}×{lay['cols']} gap {lay['gapX']}/{lay['gapY']} conf {lay['confidence']}"
        elif tag in ("flex-col", "flex-row"):
            tag = f"{tag} gap {lay.get('gap')} conf {lay['confidence']}"
        txt = f" · {n['texts'][0]['value'][:40]!r}" if n["texts"] else ""
        lines.append(
            f"{'  ' * depth}{n['kind']:6} {n['name'][:34]:34} [{n['x']},{n['y']} {n['w']}×{n['h']}] {tag}{txt}"
        )
        for c in n["children"]:
            rec(c, depth + 1)

    rec(root, 0)
    return lines


def report(root, res, board_w, board_h, src, visibility=None):
    nodes = res["nodes"]
    kinds = Counter(n["kind"] for _, n in nodes)
    L = []
    A = L.append
    A(f"# design IR — {os.path.basename(src)}\n")
    A(f"- 画板 {board_w}×{board_h}，节点 {len(nodes)}，最大深度 {max(d for d, _ in nodes)}")
    A(f"- 类型分布：{', '.join(f'{k} {v}' for k, v in kinds.most_common())}")
    A(f"- 布局：grid {sum(1 for _,n in nodes if n['layout']['kind']=='grid')}，"
      f"flex {sum(1 for _,n in nodes if n['layout']['kind'].startswith('flex'))}，"
      f"absolute {sum(1 for _,n in nodes if n['layout']['kind']=='absolute')}，"
      f"leaf {sum(1 for _,n in nodes if n['layout']['kind']=='leaf')}")

    # 布局指纹（只列有子节点且非 leaf 的）
    A("\n## 布局指纹（按面积降序，全量见 layout.json）\n")
    A("| 节点 | 尺寸 | 文法 | 参数 | 置信 | 证据 |")
    A("|---|---|---|---|---|---|")
    fp = [n for _, n in nodes if n["layout"]["kind"] != "leaf" and n["w"] * n["h"] > 2500]
    fp.sort(key=lambda n: -n["w"] * n["h"])
    for n in fp[:80]:
        lay = n["layout"]
        params = ""
        if lay["kind"] == "grid":
            params = f"{lay['rows']}×{lay['cols']}, gap {lay['gapX']}/{lay['gapY']}, cell {lay['cell']['w']}×{lay['cell']['h']}"
        elif lay["kind"].startswith("flex"):
            params = f"gap {lay.get('gap')}"
        A(
            f"| {n['name'][:24] or n['id']} | {n['w']}×{n['h']} | {lay['kind']} | {params} | {lay['confidence']} | "
            + "；".join(lay.get("evidence", []))[:110]
            + " |"
        )

    A("\n## 文本样式\n")
    A("| family | weight | size | lineHeight | 取整 | 颜色 | align | 次数 |")
    A("|---|---|---|---|---|---|---|---|")
    for (fam, w, sz, lh, lhi, color, align), c in style_table(nodes):
        flag = " ⚠️" if lh and abs(lh - lhi) > 0.2 else ""
        A(f"| {fam} | {w} | {sz} | {lh}{flag} | {lhi} | {color} | {align} | {c} |")

    fills, texts, borders = palette(nodes)
    A("\n## 色板\n")
    A("- 填充：" + "，".join(f"`{c}`×{n}" for c, n in fills[:12]))
    A("- 文字：" + "，".join(f"`{c}`×{n}" for c, n in texts[:12]))
    A("- 描边：" + "，".join(f"`{c}`/{s}/{w}px×{n}" for (c, s, w), n in borders[:10]))

    A("\n## 组件聚类（结构签名）\n")
    A("| 名称 | 尺寸 | 实例数 | 子树 | 文本数 | 布局 |")
    A("|---|---|---|---|---|---|")
    for c in res["components"][:12]:
        A(f"| {c['exampleName']} | {c['size']} | **{c['count']}** | {len(c['signature']['children'])} | {c['signature']['texts']} | {c['layout']} |")

    A("\n## 图片槽（basic.type = image，内容需从 PNG 裁或用色块占位）\n")
    for n in res["images"][:20]:
        A(f"- {n['name']} @ ({n['x']}, {n['y']}) {n['w']}×{n['h']} radius={n['radius']}")
    A(f"\n共 {len(res['images'])} 个")

    A("\n## 结构语义\n")
    A(f"- 分割线：{len(res['separators'])}")
    for n in res["separators"][:8]:
        A(f"  - @({n['x']},{n['y']}) {n['w']}×{n['h']} fill={n['fill']}")
    A(f"- 图标簇：{len(res['icons'])}（≤48px 且只含矢量）")
    A(f"- 空容器（无填充/文字/边框，可能是图片槽或占位）：{len(res['empty'])}")

    A("\n## 装饰候选（需人/模型确认）\n")
    for n, why in res["decorations"][:10]:
        A(f"- {n['name']} @({n['x']},{n['y']}) {n['w']}×{n['h']} — {why}")

    if visibility:
        hidden = [v for v in visibility if not v["rendered"]]
        A("\n## 可见性核查（PNG 上是否真的渲染）\n")
        A(f"- 文本节点 {len(visibility)} 个，**未渲染 {len(hidden)} 个**（导出物里存在 PNG 上没有的图层）")
        for v in hidden[:20]:
            A(f"- `{v['text']}` @({v['box'][0]:.0f},{v['box'][1]:.0f}) — darkRatio {v['darkRatio']}")
        if hidden:
            A("- 这些不要画进页面：通常是未使用的组件变体或隐藏状态。")
        else:
            A("- 全部文本在 PNG 上都有像素，未发现隐藏图层。")

    A("\n## 脏数据 / 需复核\n")
    dirty = [n for _, n in nodes if any(abs(n[k] - round(n[k])) > 0.05 for k in ("x", "y", "w", "h"))]
    A(f"- 浮点坐标：{len(dirty)}/{len(nodes)} 个（渲染时按 0.5px 取整）")
    A(f"- 行高非整数：{sum(1 for _,n in nodes for t in n['texts'] if t['lineHeightInt'] != t['lineHeight'])} 处（用取整值）")
    A(f"- 圆角哨兵值（导出器钳制）：{sum(1 for _,n in nodes if isinstance(n['radius'], float) and n['radius'] > 40)} 处")
    A(f"- 零尺寸节点：{sum(1 for _,n in nodes if n['w'] < 1 or n['h'] < 1)} 个（图标碎片，丢弃）")
    A("- 文本大小写：导出文本与 PNG 渲染可能不一致，**文本以 PNG 为准**（不要用导出文本回改设计）")
    return "\n".join(L)


# ---------------------------------------------------------------- main


def invariants(nodes):
    """从 IR 里抽出"设计不变量"：直接对应 CSS token 的那组数字"""
    def num(v):
        return int(v) if isinstance(v, float) and v.is_integer() else v

    grids = [n["layout"] for _, n in nodes if n["layout"]["kind"] == "grid"]
    cols = Counter(g["cols"] for g in grids)
    gaps_x = Counter(num(g["gapX"]) for g in grids)
    gaps_y = Counter(num(g["gapY"]) for g in grids if g["rows"] > 1)
    cells = Counter(f"{num(g['cell']['w'])}×{num(g['cell']['h'])}" for g in grids)
    pads = Counter(f"{num(g['padding']['left'])}/{num(g['padding']['right'])}" for g in grids)
    flex_gaps = Counter(
        num(n["layout"]["gap"])
        for _, n in nodes
        if n["layout"]["kind"] in ("flex-col", "flex-row") and isinstance(n["layout"].get("gap"), (int, float))
    )
    radii = Counter(str(num(n["radius"])) if not isinstance(n["radius"], str) else n["radius"]
                    for _, n in nodes if n["radius"] and n["w"] > 40)
    stacks = [
        n
        for _, n in nodes
        if n["layout"]["kind"] == "flex-col" and len(n["children"]) >= 5 and n["w"] > 1400
    ]
    stacks.sort(key=lambda n: -len(n["children"]))
    gutters = Counter(n["x"] for _, n in nodes if n["w"] > 1000 and n["x"] < 400)
    return {
        "boardGutter": num(gutters.most_common(1)[0][0]) if gutters else None,
        "dominantGrid": {
            "cols": cols.most_common(1)[0][0] if cols else None,
            "gapX": gaps_x.most_common(1)[0][0] if gaps_x else None,
            "gapY": gaps_y.most_common(1)[0][0] if gaps_y else None,
            "cell": cells.most_common(1)[0][0] if cells else None,
            "cardPadding": pads.most_common(1)[0][0] if pads else None,
            "instances": len(grids),
        },
        "sectionStack": {
            "children": len(stacks[0]["children"]) if stacks else 0,
            "gap": num(stacks[0]["layout"].get("gap")) if stacks else None,
            "size": f"{num(stacks[0]['w'])}×{num(stacks[0]['h'])}" if stacks else None,
        },
        "flexGaps": [[g, c] for g, c in flex_gaps.most_common(6)],
        "radii": [[r, c] for r, c in radii.most_common(6)],
    }


def instances_payload(groups):
    """把同构组件实例导出成数据数组：文本按视觉序、图片槽、顶部描边色"""
    out = {}
    for key, group in groups.items():
        if len(group) < 2:
            continue
        rows = []
        for n in group:
            texts = []
            for _, m in collect(n, []):
                for t in m["texts"]:
                    texts.append({"v": t["value"], "size": t["size"], "weight": t["weight"],
                                  "color": t["color"], "x": m["x"], "y": m["y"]})
            texts.sort(key=lambda t: (round(t["y"], 1), round(t["x"], 1)))
            imgs = [
                {"x": m["x"], "y": m["y"], "w": m["w"], "h": m["h"]}
                for _, m in collect(n, [])
                if m.get("semantic") == "image"
            ]
            top = next((b for b in n["borders"] if b["side"] == "top"), None)
            rows.append(
                {
                    "id": n["id"],
                    "box": [n["x"], n["y"], n["w"], n["h"]],
                    "texts": [t["v"] for t in texts],
                    "textStyles": [{"size": t["size"], "weight": t["weight"], "color": t["color"]} for t in texts[:4]],
                    "images": imgs,
                    "accent": top["color"] if top else None,
                }
            )
        rows.sort(key=lambda r: (round(r["box"][1], 1), round(r["box"][0], 1)))
        out[key] = {"count": len(rows), "name": group[0]["name"], "items": rows}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("--out", default="design-ir")
    ap.add_argument("--board", type=int, default=None)
    ap.add_argument("--gutter", type=int, default=None)
    ap.add_argument("--tol", type=float, default=2.0)
    ap.add_argument("--png", default=None, help="设计图 PNG：核查文本是否真的渲染（隐藏图层检测）")
    ap.add_argument("--png-scale", type=float, default=None, help="PNG 相对画板的倍率，缺省按 PNG 宽/画板宽")
    a = ap.parse_args()

    raw = json.load(open(a.source, encoding="utf-8-sig"))
    root = normalize(raw)
    board_w = a.board or int(root["w"])
    board_h = int(root["h"])
    gutter = a.gutter if a.gutter is not None else round((board_w - min(root["w"], 1680)) / 2) or 120

    res = analyze(root, board_w, gutter, a.tol)
    nodes = res["nodes"]

    os.makedirs(a.out, exist_ok=True)
    json.dump(root, open(os.path.join(a.out, "ir.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(
        {n["id"]: {"name": n["name"], "kind": n["kind"], "layout": n["layout"], "box": [n["x"], n["y"], n["w"], n["h"]]}
         for _, n in nodes},
        open(os.path.join(a.out, "layout.json"), "w", encoding="utf-8"),
        ensure_ascii=False,
        indent=1,
    )
    visibility = []
    if a.png:
        try:
            from PIL import Image
        except ImportError:
            raise SystemExit("design_ir 的 --png 需要 Pillow：pip install Pillow")

        scale = a.png_scale or (Image.open(a.png).width / board_w)
        visibility = visibility_check(nodes, a.png, scale)
        json.dump(
            visibility,
            open(os.path.join(a.out, "visibility.json"), "w", encoding="utf-8"),
            ensure_ascii=False,
            indent=1,
        )
        hidden = [v for v in visibility if not v["rendered"]]
        print(f"可见性：{len(visibility)} 个文本节点，{len(hidden)} 个在 PNG 上未渲染")
        for v in hidden[:8]:
            print(f"  · {v['text']!r} @({v['box'][0]:.0f},{v['box'][1]:.0f})")

    open(os.path.join(a.out, "tree.txt"), "w", encoding="utf-8").write("\n".join(tree_lines(root)))
    open(os.path.join(a.out, "report.md"), "w", encoding="utf-8").write(
        report(root, res, board_w, board_h, a.source, visibility)
    )

    masks = [{"x": n["x"], "y": n["y"], "w": n["w"], "h": n["h"], "kind": "image"} for n in res["images"]]
    masks += [{"x": n["x"], "y": n["y"], "w": n["w"], "h": n["h"], "kind": "decoration"} for n, _ in res["decorations"]]
    json.dump({"slots": masks}, open(os.path.join(a.out, "masks.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    json.dump(
        {
            "separators": [{"x": n["x"], "y": n["y"], "w": n["w"], "h": n["h"], "fill": n["fill"]} for n in res["separators"]],
            "icons": [{"x": n["x"], "y": n["y"], "w": n["w"], "h": n["h"], "name": n["name"]} for n in res["icons"]],
        },
        open(os.path.join(a.out, "semantics.json"), "w", encoding="utf-8"),
        ensure_ascii=False,
        indent=1,
    )

    json.dump(
        invariants(nodes),
        open(os.path.join(a.out, "invariants.json"), "w", encoding="utf-8"),
        ensure_ascii=False,
        indent=1,
    )

    json.dump(
        instances_payload(res["groups"]),
        open(os.path.join(a.out, "instances.json"), "w", encoding="utf-8"),
        ensure_ascii=False,
        indent=1,
    )

    grids = [n for _, n in nodes if n["layout"]["kind"] == "grid"]
    low = [
        n
        for _, n in nodes
        if n["layout"]["kind"] not in ("leaf", "stack", "overlay")
        and n["layout"]["confidence"] < 0.8
        and n["w"] * n["h"] > 2000
    ]
    kinds = Counter(n["layout"]["kind"] for _, n in nodes)
    print(f"nodes {len(nodes)} | grid {len(grids)} | 低置信 {len(low)} | 组件 {len(res['components'])} | 图片槽 {len(res['images'])} | 装饰候选 {len(res['decorations'])}")
    print("文法分布：", dict(kinds))
    print("→", a.out)
    for n in sorted(grids, key=lambda x: -x["w"] * x["h"])[:8]:
        l = n["layout"]
        print(f"  [{l['rows']}×{l['cols']} gap {l['gapX']}/{l['gapY']} conf {l['confidence']}] {n['name']} {n['w']}×{n['h']}")
    for n in low[:10]:
        print(f"  ⚠️ low conf {n['layout']['confidence']} {n['name']} {n['w']}×{n['h']} {n['layout']['evidence']}")


if __name__ == "__main__":
    sys.exit(main())
