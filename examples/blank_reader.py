"""Opt-in synthetic reader for trying blank probing; no input automation."""
import argparse
import numpy as np
from PIL import Image, ImageDraw
from vdc.app.main import enable_dpi_awareness
from vdc.gui.images import pixmap
from PySide6.QtWidgets import QApplication, QMainWindow, QLabel, QScrollArea


def main():
    parser = argparse.ArgumentParser(description="打开有大面积留白的合成阅读器，供手动框选测试。")
    parser.add_argument("--blank-height", type=int, default=900, choices=range(100, 3001), metavar="100..3000")
    parser.add_argument("--height-model", action="store_true", help="前三页提供尺寸样本，第四页含大面积留白")
    args = parser.parse_args()
    enable_dpi_awareness()
    app = QApplication([])
    height = args.blank_height + 600
    image = Image.new("RGB", (640, height), (170, 170, 170))
    draw = ImageDraw.Draw(image)
    draw.rectangle((35, 20, 604, height-21), fill="white")
    for i in range(10):
        draw.text((65, 45+i*17), f"Before blank / reference line {i+1}: document capture sample", fill=(25, 25, 25))
        draw.text((65, 240+args.blank_height+i*17), f"After blank / retained content {i+1}: verify before joining", fill=(25, 25, 25))
    if args.height_model:
        image = Image.new("RGB", (640, 2460), (170, 170, 170))
        draw = ImageDraw.Draw(image)
        for page in range(4):
            top = 12+page*612
            draw.rectangle((35, top, 604, top+599), fill="white")
            for i, y in enumerate(range(30, 570 if page < 3 else 200, 17)):
                draw.text((65, top+y), f"Page {page+1} / line {i+1}: document geometry sample {page*101+i*17}", fill=(25, 25, 25))
                draw.line((65, top+y+12, 100+(i*37+page*53)%440, top+y+12), fill=(90, 90, 90))
    label = QLabel()
    label.setPixmap(pixmap(np.array(image)))
    area = QScrollArea()
    area.setWidget(label)
    window = QMainWindow()
    window.setWindowTitle("VDC 页高连接测试 · 4 页同尺寸合成内容" if args.height_model else
                          f"VDC 空白测试阅读器 · 留白约 {args.blank_height} px · 仅合成内容")
    window.setCentralWidget(area)
    window.resize(680, 340 if args.height_model else 480)
    window.show()
    app.exec()


if __name__ == "__main__":
    main()
