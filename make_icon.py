# -*- coding: utf-8 -*-
"""生成一个现代风格的 YouTube 下载器图标 app.ico（多尺寸 16~256）。

设计：圆角方形渐变底（YouTube 红）+ 白色「下载箭头」字形（向下箭头落入托盘）。
"""
from PIL import Image, ImageDraw

# 渐变配色（上 -> 下）
TOP = (255, 70, 70)
BOTTOM = (196, 12, 12)


def round_rect_gradient(size):
    """画带竖直渐变的圆角方形，返回 RGBA 图。"""
    s = size
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    radius = int(s * 0.22)
    # 逐行填充竖直渐变
    for y in range(s):
        t = y / (s - 1)
        r = int(TOP[0] + (BOTTOM[0] - TOP[0]) * t)
        g = int(TOP[1] + (BOTTOM[1] - TOP[1]) * t)
        b = int(TOP[2] + (BOTTOM[2] - TOP[2]) * t)
        draw.line([(0, y), (s - 1, y)], fill=(r, g, b, 255))
    # 圆角遮罩：四个角挖透明
    mask = Image.new("L", (s, s), 255)
    md = ImageDraw.Draw(mask)
    md.polygon([(0, 0), (s, 0), (s, s), (0, s)], fill=255)
    # 用黑色圆角矩形覆盖四角（白色=保留，黑色=透明）
    corner = Image.new("L", (s, s), 0)
    cd = ImageDraw.Draw(corner)
    cd.rounded_rectangle([0, 0, s - 1, s - 1], radius=radius, fill=255)
    mask = corner
    img.putalpha(mask)
    return img


def draw_glyph(img):
    """在图标中央画白色下载箭头（向下箭头 + 底部托盘）。"""
    s = img.size[0]
    draw = ImageDraw.Draw(img)
    cx = s / 2
    white = (255, 255, 255, 255)
    # 几何比例
    shaft_w = s * 0.13
    top_y = s * 0.24
    head_y = s * 0.52
    tray_y = s * 0.74
    # 箭头竖杆
    draw.rectangle(
        [cx - shaft_w / 2, top_y, cx + shaft_w / 2, head_y],
        fill=white,
    )
    # 箭头三角头（向下）
    hw = s * 0.20
    draw.polygon(
        [(cx - hw, head_y - shaft_w * 0.2),
         (cx + hw, head_y - shaft_w * 0.2),
         (cx, head_y + s * 0.12)],
        fill=white,
    )
    # 底部托盘（横杆 + 两端上翘）
    tray_w = s * 0.46
    tray_h = s * 0.045
    lx = cx - tray_w / 2
    rx = cx + tray_w / 2
    draw.rectangle([lx, tray_y, rx, tray_y + tray_h], fill=white)
    up = s * 0.07
    draw.rectangle([lx, tray_y - up, lx + tray_h, tray_y], fill=white)
    draw.rectangle([rx - tray_h, tray_y - up, rx, tray_y], fill=white)
    return img


def make_ico(path, sizes=(16, 24, 32, 48, 64, 128, 256)):
    frames = []
    for sz in sizes:
        base = round_rect_gradient(sz)
        base = draw_glyph(base)
        frames.append(base)
    # ICO 要求把最大尺寸作为首帧，其余作为追加帧（不传 sizes 参数，
    # 由各帧自身尺寸决定 ICO 内嵌档位）
    frames.sort(key=lambda f: f.width, reverse=True)
    frames[0].save(path, append_images=frames[1:])
    print("saved", path, "sizes", sizes)


if __name__ == "__main__":
    make_ico("app.ico")
