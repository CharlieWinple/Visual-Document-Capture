import numpy as np
from PySide6.QtGui import QImage, QPixmap


def pixmap(pixels):
    pixels = np.ascontiguousarray(pixels)
    h, w = pixels.shape[:2]
    # Copy before returning: the NumPy allocation is owned by the caller.
    return QPixmap.fromImage(QImage(pixels.data, w, h, pixels.strides[0], QImage.Format.Format_RGB888).copy())
