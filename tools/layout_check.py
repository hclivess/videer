#!/usr/bin/env python3
"""
Layout check, run before tagging (python tools/layout_check.py): no visible control may be shorter than its
own content — at the default window size and at the minimum — and no widget class may own an attribute named
after a Qt method (`self.metric`, `self.result`), which turns every repaint into a TypeError.
Exits non-zero with a list when either is found.
"""
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["VIDEER_NO_STATE"] = "1"
if sys.platform == "win32":
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QLabel, QPushButton,  # noqa: E402
                               QSpinBox, QDoubleSpinBox, QWidget)

CHECKED = (QLabel, QComboBox, QSpinBox, QDoubleSpinBox, QCheckBox, QPushButton)


def cramped(root: QWidget, where: str) -> list:
    found = []
    for widget in root.findChildren(QWidget):
        if not isinstance(widget, CHECKED) or not widget.isVisible():
            continue
        if widget.hasHeightForWidth():
            needed = widget.heightForWidth(widget.width())
        else:
            needed = widget.sizeHint().height()
        if needed > 0 and widget.height() + 1 < needed:
            text = (widget.text() if hasattr(widget, 'text') else '') or type(widget).__name__
            found.append(f"{where}: {type(widget).__name__} '{text[:50]}' is {widget.height()}px, needs {needed}px")
    return found


def shadowed_attributes() -> list:
    qt_names = set(dir(QDialog)) | set(dir(QWidget))
    found = []
    for folder, _dirs, files in os.walk(os.path.join(ROOT, "modules")):
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(folder, name)
            tree = ast.parse(open(path, encoding="utf-8").read())
            for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
                bases = {getattr(b, 'id', getattr(b, 'attr', '')) for b in cls.bases}
                if not any(b.startswith('Q') for b in bases):
                    continue
                for node in ast.walk(cls):
                    if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                            and node.value.id == 'self' and isinstance(node.ctx, ast.Store)
                            and node.attr in qt_names):
                        found.append(f"{os.path.relpath(path, ROOT)}: {cls.name}.{node.attr} shadows a Qt method")
    return found


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    import main as app_main
    from modules.vapoursynth_installer import VapourSynthSetupDialog
    from modules.ffmpeg_installer import FFmpegSetupDialog

    win = app_main.MainWindow()
    win.show()
    ui = win.ui_manager
    ui._select_deinterlacer('qtempgaussmc')               # the VapourSynth panel is only shown for it
    problems = []
    for label, size in (("default", win.size()), ("minimum", win.minimumSizeHint())):
        win.resize(size)
        for index in range(ui.tabs.count()):
            ui.tabs.setCurrentIndex(index)
            app.processEvents()
            problems += cramped(win, f"main window ({label}), tab {ui.tabs.tabText(index)}")
    for dialog_class in (VapourSynthSetupDialog, FFmpegSetupDialog):
        dialog = dialog_class(win)
        dialog.show()
        app.processEvents()
        problems += cramped(dialog, dialog_class.__name__)
        dialog.close()
    problems += shadowed_attributes()

    for line in problems:
        print(line)
    print(f"layout check: {len(problems)} problem(s)")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
