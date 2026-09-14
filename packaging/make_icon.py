#!/usr/bin/env python3
"""
生成 CodeBuddy2API 的 macOS 应用图标。

图形：一颗有切割面的钻石，周围环绕放射状星光。

产出：
  packaging/icon_1024.png   1024×1024 主图
  packaging/icon.icns       供 PyInstaller 打包使用

用法：
  python3 packaging/make_icon.py
"""

import os
import subprocess
import sys

from PIL import Image, ImageChops, ImageDraw, ImageFilter

SIZE = 1024  # 主图边长
SS = 4       # 超采样倍数：先在 4 倍尺寸画，再缩回去，得到干净的抗锯齿边缘

# ---- Apple 图标规范参数 ----
# macOS Big Sur 以后的图标是"圆角方形"，圆角半径 = 边长 × 22.37%。
# 另外图标本体不铺满画布：四周要留出透明边距，否则在 Dock 里会比别的图标大一圈。
CORNER_RATIO = 0.2237
BODY_RATIO = 0.80      # 本体占画布比例（留 10% 边距 × 2）

# ---- 品牌配色 ----
# 沿用原图标的蓝色，但改成纵向渐变，让平面有纵深。
BLUE_TOP = (74, 144, 255)     # #4a90ff 稍亮
BLUE_BOT = (30, 92, 224)      # #1e5ce0 稍深
GLYPH = (255, 255, 255)
# 星光用极浅的暖白，纯白会和钻石糊在一起
SPARKLE = (255, 252, 236)

# ---- 彩虹光晕 ----
# 在蓝底上叠一层七彩辉光，像阳光穿过棱镜。
# 颜色按光谱顺序排列，相邻色相靠得近，过渡才自然、不会出现色带。
RAINBOW = [
    (255, 92, 106),    # 红
    (255, 148, 74),    # 橙
    (255, 213, 82),    # 黄
    (104, 224, 148),   # 绿
    (86, 200, 244),    # 青
    (126, 152, 255),   # 蓝
    (186, 128, 246),   # 紫
]


def lerp(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def make_gradient(size, top, bottom):
    """纵向线性渐变。"""
    img = Image.new("RGB", (1, size))
    px = img.load()
    for y in range(size):
        px[0, y] = lerp(top, bottom, y / max(1, size - 1))
    return img.resize((size, size), Image.BILINEAR)


def rounded_mask(size, radius):
    """圆角方形遮罩，超采样后缩回以获得平滑边缘。"""
    big = Image.new("L", (size * SS, size * SS), 0)
    d = ImageDraw.Draw(big)
    d.rounded_rectangle(
        [0, 0, size * SS - 1, size * SS - 1],
        radius=radius * SS,
        fill=255,
    )
    return big.resize((size, size), Image.LANCZOS)


def rainbow_glow(size):
    """彩虹光晕。

    做法：先画一条横向的光谱渐变，再把它揉成"斜向的柔软光带"，
    叠在蓝底上。关键是让相邻颜色充分交叠——直接按色相分段画会
    出现明显色带（banding），所以这里用较宽的径向权重去混合。

    返回 (RGB 图, 强度遮罩)。
    """
    s = size

    # --- 1. 横向光谱 ---
    spec = Image.new("RGB", (s, 1))
    px = spec.load()
    n = len(RAINBOW)
    for x in range(s):
        t = x / (s - 1) * (n - 1)
        i = min(int(t), n - 2)
        px[x, 0] = lerp(RAINBOW[i], RAINBOW[i + 1], t - i)
    spec = spec.resize((s, s), Image.BILINEAR)

    # --- 2. 斜向拉开：旋转后铺满，避免出现空角 ---
    spec = spec.rotate(-28, resample=Image.BICUBIC, expand=False)
    spec = spec.resize((int(s * 1.6), int(s * 1.6)), Image.BICUBIC)
    spec = spec.crop(((spec.width - s) // 2, (spec.height - s) // 2,
                      (spec.width - s) // 2 + s, (spec.height - s) // 2 + s))

    # --- 3. 强度遮罩 ---
    # 左上偏实、右下渐隐，做出"图内渐淡"的效果。
    # 用对角线性渐变再高斯模糊，过渡才自然。
    m = Image.new("L", (s, s), 0)
    md = ImageDraw.Draw(m)
    # 逐行画渐变（比逐像素快，且足够平滑）
    for y in range(s):
        v = int(255 * (1 - y / (s - 1)) * 0.85 + 30)
        md.line([(0, y), (s, y)], fill=max(0, min(255, v)))
    m = m.rotate(-28, resample=Image.BICUBIC, expand=False)
    m = m.filter(ImageFilter.GaussianBlur(s * 0.10))
    # 压低整体强度，让彩虹是"一抹"而不是盖住蓝底
    m = m.point(lambda v: int(v * 0.60))

    # 中心稍亮、边缘收敛，避免彩虹糊到圆角边上
    vign = Image.new("L", (s, s), 0)
    vd = ImageDraw.Draw(vign)
    vd.ellipse([-s * 0.15, -s * 0.15, s * 1.15, s * 1.15], fill=255)
    vign = vign.filter(ImageFilter.GaussianBlur(s * 0.14))
    m = ImageChops.multiply(m, vign)

    return spec, m


def draw_diamond(size):
    """一颗明亮切割（brilliant cut）钻石的正面视图。

    结构：上部为冠部（桌面 + 左右冠面），下部为亭部（收成尖底）。
    用不同灰度区分各个刻面，缩到小尺寸时仍能看出"有切面"的立体感。
    返回单通道亮度图，由调用方着色。
    """
    s = size * SS
    layer = Image.new("L", (s, s), 0)
    d = ImageDraw.Draw(layer)

    cx = s / 2
    # 以下全部是"像素"值（相对画布）。统一单位，避免比例/像素混用。
    half_w = s * 0.235       # 腰围半宽
    table_half = half_w * 0.46   # 桌面半宽
    top = s * 0.315          # 桌面顶边
    girdle = s * 0.455       # 腰围线：冠部与亭部交界
    culet = s * 0.735        # 底尖

    # 各刻面用不同灰度，形成切割的光影层次
    FACET = {
        'table': 255,        # 桌面最亮
        'crown_l': 214,      # 左冠面
        'crown_r': 236,      # 右冠面（受光侧更亮）
        'pav_l': 176,        # 左亭面
        'pav_r': 208,        # 右亭面
        'pav_c': 232,        # 中亭面
    }

    def poly(pts, v):
        # x 是相对中心的像素偏移，y 是绝对像素坐标。两者都已是像素。
        d.polygon([(cx + x, y) for x, y in pts], fill=v)

    # --- 亭部（下半）---
    poly([(-half_w, girdle), (0, girdle), (0, culet)], FACET['pav_l'])
    poly([(0, girdle), (half_w, girdle), (0, culet)], FACET['pav_r'])
    poly([(-table_half, girdle), (table_half, girdle), (0, culet)], FACET['pav_c'])

    # --- 冠部（上半）---
    # 左冠面：从桌面左边斜下到腰围左端
    poly([(-table_half, top), (0, top), (0, girdle), (-half_w, girdle)],
         FACET['crown_l'])
    # 右冠面
    poly([(0, top), (table_half, top), (half_w, girdle), (0, girdle)],
         FACET['crown_r'])
    # 桌面压在冠面之上，形成正中的高光平面
    poly([(-table_half, top), (table_half, top), (table_half * 0.88, girdle),
          (-table_half * 0.88, girdle)], FACET['table'])

    return layer.resize((size, size), Image.LANCZOS)


def draw_sparkle(size, cx, cy, r, arms=4, thickness=0.20, sharp=2.6):
    """一颗四角星（starburst）。

    用两段窄椭圆拼成：长轴决定臂长，短轴决定粗细。
    sharp 越大星芒越尖，像镜头里的高光。
    """
    s = size
    layer = Image.new("L", (s, s), 0)
    d = ImageDraw.Draw(layer)
    w = r * thickness

    for angle in (0, 90) if arms == 4 else (0, 45, 90, 135):
        # 在 2s 画布上以 (s,s) 为原点画，旋转后再裁回 s×s，
        # 这样星芒始终以 (cx,cy) 为中心。
        big = Image.new("L", (s * 2, s * 2), 0)
        sd = ImageDraw.Draw(big)
        sd.polygon(
            [(s, s - r), (s + w, s), (s, s + r), (s - w, s)],
            fill=255,
        )
        big = big.rotate(angle, resample=Image.BICUBIC, center=(s, s))
        star = big.crop((s - cx, s - cy, s - cx + s, s - cy + s))
        layer = ImageChops.lighter(layer, star)

    # 中心加一个小圆点，让星芒交汇处更实
    d = ImageDraw.Draw(layer)
    rr = r * 0.16
    d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=255)
    return layer


def starburst(size):
    """围绕钻石的一圈星光。

    按"大星在外圈、小星在内圈"排布，避免所有星芒一样大显得呆板。
    角度刻意不完全对称，看起来更自然。
    """
    import math

    s = size
    out = Image.new("L", (s, s), 0)
    c = s / 2

    # (角度°, 距中心比例, 相对大小)
    specs = [
        (15, 0.92, 1.00), (68, 0.80, 0.62), (124, 0.94, 0.84),
        (198, 0.82, 0.58), (244, 0.92, 0.90), (298, 0.78, 0.55),
        (338, 0.66, 0.40), (162, 0.62, 0.36),
    ]
    for ang, dist, scale in specs:
        r = s * 0.055 * scale
        x = c + math.cos(math.radians(ang)) * c * dist
        y = c + math.sin(math.radians(ang)) * c * dist
        # 先单独画一颗居中的星，再整幅平移过去（用 paste，避免 offset 的取整误差）
        sp = draw_sparkle(s, c, c, r)
        shifted = Image.new("L", (s, s), 0)
        shifted.paste(sp, (int(round(x - c)), int(round(y - c))))
        out = ImageChops.lighter(out, shifted)

    return out


def build():
    body = round(SIZE * BODY_RATIO)
    radius = round(body * CORNER_RATIO)

    canvas = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))

    # --- 1. 投影：让图标从背景上"浮起来" ---
    shadow = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    sh = ImageDraw.Draw(shadow)
    off = round(SIZE * 0.018)
    sh.rounded_rectangle(
        [(SIZE - body) // 2, (SIZE - body) // 2 + off,
         (SIZE + body) // 2, (SIZE + body) // 2 + off],
        radius=radius, fill=(16, 42, 100, 90),
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(SIZE * 0.022))
    canvas = Image.alpha_composite(canvas, shadow)

    # --- 2. 本体：渐变 + 圆角遮罩 ---
    face = make_gradient(body, BLUE_TOP, BLUE_BOT).convert("RGBA")
    face.putalpha(rounded_mask(body, radius))

    # --- 3. 顶部高光：模拟玻璃材质 ---
    gloss = Image.new("L", (body, body), 0)
    gd = ImageDraw.Draw(gloss)
    gd.ellipse(
        [-body * 0.25, -body * 0.95, body * 1.25, body * 0.52],
        fill=70,
    )
    gloss = gloss.filter(ImageFilter.GaussianBlur(body * 0.06))
    gloss = Image.composite(gloss, Image.new("L", (body, body), 0),
                            rounded_mask(body, radius))
    face = Image.alpha_composite(face, Image.merge("RGBA", (
        Image.new("L", (body, body), 255),
        Image.new("L", (body, body), 255),
        Image.new("L", (body, body), 255),
        gloss,
    )))

    # --- 4. 星光：先在底层铺一层柔光，营造"闪耀"的氛围 ---
    star = starburst(body)
    halo = star.filter(ImageFilter.GaussianBlur(body * 0.030))
    white = Image.new("L", (body, body), 255)
    face = Image.alpha_composite(face, Image.merge("RGBA", (
        white, white, white, halo.point(lambda v: int(v * 0.55)),
    )))

    # --- 5. 钻石：带外发光，小尺寸下也能从背景里跳出来 ---
    diamond = draw_diamond(body)
    dglow = diamond.filter(ImageFilter.GaussianBlur(body * 0.020))
    face = Image.alpha_composite(face, Image.merge("RGBA", (
        white, white, white, dglow.point(lambda v: int(v * 0.50)),
    )))
    face = Image.alpha_composite(face, Image.merge("RGBA", (
        Image.new("L", (body, body), GLYPH[0]),
        Image.new("L", (body, body), GLYPH[1]),
        Image.new("L", (body, body), GLYPH[2]),
        diamond,
    )))

    # --- 6. 星光实体叠在钻石之上，形成环绕感 ---
    face = Image.alpha_composite(face, Image.merge("RGBA", (
        Image.new("L", (body, body), SPARKLE[0]),
        Image.new("L", (body, body), SPARKLE[1]),
        Image.new("L", (body, body), SPARKLE[2]),
        star,
    )))

    # 统一裁一次圆角。
    # 必须在所有层都合成完之后再裁：否则符号的发光会溢出图标轮廓，
    # 在浅色背景上形成一圈脏光晕，轮廓也不再干净。
    face.putalpha(ImageChops.multiply(face.split()[3], rounded_mask(body, radius)))
    canvas.paste(face, ((SIZE - body) // 2, (SIZE - body) // 2), face)

    out_png = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon_1024.png")
    canvas.save(out_png)
    return out_png, canvas


def to_icns(png_path, canvas):
    """用 iconutil 打包成 .icns（macOS 原生，质量最好）。"""
    here = os.path.dirname(os.path.abspath(__file__))
    iconset = os.path.join(here, "icon.iconset")
    os.makedirs(iconset, exist_ok=True)

    # Apple 要求的全套尺寸
    specs = [
        (16, "icon_16x16.png"), (32, "icon_16x16@2x.png"),
        (32, "icon_32x32.png"), (64, "icon_32x32@2x.png"),
        (128, "icon_128x128.png"), (256, "icon_128x128@2x.png"),
        (256, "icon_256x256.png"), (512, "icon_256x256@2x.png"),
        (512, "icon_512x512.png"), (1024, "icon_512x512@2x.png"),
    ]
    for px, name in specs:
        canvas.resize((px, px), Image.LANCZOS).save(os.path.join(iconset, name))

    out_icns = os.path.join(here, "icon.icns")
    subprocess.run(["iconutil", "-c", "icns", iconset, "-o", out_icns], check=True)

    for name in os.listdir(iconset):
        os.remove(os.path.join(iconset, name))
    os.rmdir(iconset)
    return out_icns


def main():
    png, canvas = build()
    print(f"已生成 {png}")

    if sys.platform == "darwin":
        icns = to_icns(png, canvas)
        print(f"已生成 {icns}")
    else:
        print("非 macOS，跳过 .icns 生成（需要 iconutil）")

    # 自检：确认符合规范
    import numpy as np
    a = np.array(canvas.split()[3]) > 10
    ys, xs = a.nonzero()
    print("\n规范自检：")
    print(f"  不透明占比 {a.sum() / a.size * 100:.1f}%（旧图标 95.5%，规范约 64%）")
    print(f"  内容 bbox x {xs.min()}-{xs.max()}, y {ys.min()}-{ys.max()}")
    print(f"  边距 左{xs.min()} 右{1023 - xs.max()} 上{ys.min()} 下{1023 - ys.max()} px")


if __name__ == "__main__":
    main()
