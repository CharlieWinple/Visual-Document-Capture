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
    label = QLabel()
    label.setPixmap(pixmap(np.array(image)))
    area = QScrollArea()
    area.setWidget(label)
    window = QMainWindow()
    window.setWindowTitle(f"VDC 空白测试阅读器 · 留白约 {args.blank_height} px · 仅合成内容")
    window.setCentralWidget(area)
    window.resize(680, 480)
    window.show()
    app.exec()


if __name__ == "__main__":
    main()
