"""
Small widgets that behave the way the layouts assume they do.
"""

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QLabel, QSizePolicy


class WrapLabel(QLabel):
    """
    A word-wrapped label that asks for the height its text actually needs at the width it is given.

    A plain QLabel with setWordWrap(True) does not: it reports one line's worth of height, and a layout —
    QFormLayout above all — believes it. The text then wraps anyway and draws over the rows above and below,
    which is how the metric note in Match Source Quality ended up sitting on top of two combo boxes and being
    unreadable. Reporting the real height costs one call to the font metrics and lets every layout do its job.
    """

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        policy = QSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Minimum)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    # ------------------------------------------------------------------
    def _height_for(self, width: int) -> int:
        width = max(1, width - self.contentsMargins().left() - self.contentsMargins().right())
        rect = self.fontMetrics().boundingRect(0, 0, width, 10 ** 6,
                                               int(self.alignment()) | Qt.TextFlag.TextWordWrap,
                                               self.text())
        return rect.height() + self.contentsMargins().top() + self.contentsMargins().bottom()

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._height_for(width)

    def minimumSizeHint(self) -> QSize:
        # Whatever width it ends up with, one line is the floor; the layout asks heightForWidth for the rest
        return QSize(0, self._height_for(self.width() or 200))

    def sizeHint(self) -> QSize:
        width = self.width() or 400
        return QSize(width, self._height_for(width))

    def setText(self, text: str) -> None:
        super().setText(text)
        # The new text may need more or fewer lines than the old one, and the layout has to be told
        self.updateGeometry()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if event.oldSize().width() != event.size().width():
            self.updateGeometry()
