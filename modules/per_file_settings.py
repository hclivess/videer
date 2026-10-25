"""
Per-file settings for videer.

The queue's settings suit the batch; individual files often do not. A grainy 35mm transfer in a queue of
clean digital sources wants grain preservation and a lower CRF, an interlaced capture among progressive
files wants deinterlacing, and one oversized title wants a different container — none of which is a
reason to split the queue into several runs.

Overrides are stored sparsely on the VideoFile: only the keys a file asked to differ on are kept, so
later changes to the queue's own settings still reach every file that did not ask to differ. Each row in
the editor therefore has three states, not two — follow the queue, or override with this value.
"""

from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
                               QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox,
                               QVBoxLayout, QWidget)

from config import (AUDIO_CODECS, CRF_SCALE_MAX, DEFAULT_CRF_SCALE_MAX, ENCODING_PRESETS,
                    GRAIN_TUNE, GRAIN_UNSUPPORTED_NOTE, OUTPUT_FORMATS, PER_FILE_KEYS,
                    PER_FILE_LABELS, RESOLUTION_PRESETS, VIDEO_CODECS, quality_knob)

# The subset offered in the dialog, in the order they are shown. Everything here must also be in
# config.PER_FILE_KEYS, which is what the encoder and the queue file will accept.
EDITABLE = (
    'video_codec',
    'crf',
    'grain_mode',
    'preset',
    'audio_codec',
    'abr',
    'stereo',
    'output_format',
    'resolution_mode',
    'deinterlace',
    'tff',
    'reduce_fps',
    'ffmpeg_extras',
)

FOLLOW_QUEUE = "— follow the queue —"


class _Row:
    """One setting: a 'follow the queue' checkbox and the widget that holds the override value"""

    def __init__(self, key: str, editor: QWidget, getter, setter):
        self.key = key
        self.editor = editor
        self._get = getter
        self._set = setter
        self.override = QCheckBox(PER_FILE_LABELS.get(key, key))
        self.override.setToolTip("Tick to give this file its own value; leave clear to follow the queue.")
        self.override.toggled.connect(self.editor.setEnabled)
        self.editor.setEnabled(False)

    def value(self) -> Any:
        return self._get()

    def set_value(self, value: Any):
        self._set(value)


class PerFileSettingsDialog(QDialog):
    """
    Edit the settings of one file, or of several at once.

    With several files selected the editor starts from their common ground: a row shows a value only if
    every selected file already agrees on it, and ticking a row applies that value to all of them.
    """

    def __init__(self, parent, files: List[Any], queue_settings: Dict[str, Any]):
        super().__init__(parent)
        self.files = files
        self.queue_settings = queue_settings or {}
        self.rows: Dict[str, _Row] = {}

        many = len(files) > 1
        self.setWindowTitle(f"Settings for {len(files)} files" if many
                           else f"Settings for {files[0].filename}")
        self.setMinimumWidth(560)

        layout = QVBoxLayout(self)

        intro = QLabel(
            "Anything left unticked follows the queue's own settings, so changing the queue later still "
            "reaches this file."
            if not many else
            "Anything left unticked follows the queue's own settings. A ticked row is applied to all "
            f"{len(files)} selected files."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        group = QGroupBox("This file differs from the queue in:" if not many
                          else "These files differ from the queue in:")
        form = QFormLayout(group)
        for key in EDITABLE:
            if key not in PER_FILE_KEYS:
                continue                       # keeps the dialog honest if PER_FILE_KEYS is trimmed
            row = self._build_row(key)
            if row is None:
                continue
            self.rows[key] = row
            form.addRow(row.override, row.editor)
        layout.addWidget(group)

        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color: #8a6d3b;")
        layout.addWidget(self.hint)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        clear = QPushButton("Follow the queue for everything")
        clear.setToolTip("Drop every override on the selected file(s)")
        clear.clicked.connect(self._clear_all)
        buttons.addButton(clear, QDialogButtonBox.ButtonRole.ResetRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._load()
        if 'video_codec' in self.rows:
            self.rows['video_codec'].editor.currentIndexChanged.connect(self._sync_grain)
        if 'grain_mode' in self.rows:
            self.rows['grain_mode'].override.toggled.connect(lambda *_: self._sync_grain())
        self._sync_grain()

    # ------------------------------------------------------------------
    # Row construction
    # ------------------------------------------------------------------
    def _build_row(self, key: str) -> Optional[_Row]:
        if key == 'video_codec':
            combo = QComboBox()
            for label, value in VIDEO_CODECS:
                combo.addItem(label, value)
            return _Row(key, combo, lambda c=combo: c.currentData(),
                        lambda v, c=combo: self._select_data(c, v))
        if key == 'audio_codec':
            combo = QComboBox()
            for label, value in AUDIO_CODECS:
                combo.addItem(label, value)
            return _Row(key, combo, lambda c=combo: c.currentData(),
                        lambda v, c=combo: self._select_data(c, v))
        if key == 'crf':
            spin = QSpinBox()
            spin.setRange(0, DEFAULT_CRF_SCALE_MAX)
            spin.setToolTip("Lower is better and larger. The scale is the chosen encoder's own.")
            return _Row(key, spin, spin.value, lambda v, w=spin: w.setValue(int(v)))
        if key == 'abr':
            spin = QSpinBox()
            spin.setRange(32, 2048)
            spin.setSuffix(" kbps")
            return _Row(key, spin, spin.value, lambda v, w=spin: w.setValue(int(v)))
        if key == 'preset':
            combo = QComboBox()
            combo.addItems(ENCODING_PRESETS)
            return _Row(key, combo, combo.currentText,
                        lambda v, c=combo: self._select_text(c, v))
        if key == 'output_format':
            combo = QComboBox()
            combo.addItems(OUTPUT_FORMATS)
            return _Row(key, combo, combo.currentText,
                        lambda v, c=combo: self._select_text(c, v))
        if key == 'resolution_mode':
            combo = QComboBox()
            combo.addItems(list(RESOLUTION_PRESETS.keys()))
            return _Row(key, combo, combo.currentText,
                        lambda v, c=combo: self._select_text(c, v))
        if key == 'ffmpeg_extras':
            edit = QLineEdit()
            edit.setPlaceholderText("extra FFmpeg arguments for this file only")
            return _Row(key, edit, edit.text, lambda v, w=edit: w.setText(str(v or '')))
        if key in ('grain_mode', 'stereo', 'deinterlace', 'tff', 'reduce_fps'):
            box = QCheckBox("on")
            return _Row(key, box, box.isChecked, lambda v, w=box: w.setChecked(bool(v)))
        return None

    @staticmethod
    def _select_data(combo: QComboBox, value: Any):
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    @staticmethod
    def _select_text(combo: QComboBox, value: Any):
        index = combo.findText(str(value))
        if index >= 0:
            combo.setCurrentIndex(index)

    # ------------------------------------------------------------------
    # Loading and saving
    # ------------------------------------------------------------------
    def _common_override(self, key: str):
        """(has_override, value) across every selected file — only agreement counts as a value"""
        present = [f.overrides.get(key, None) for f in self.files]
        if any(key not in f.overrides for f in self.files):
            return False, None
        first = present[0]
        return (True, first) if all(v == first for v in present) else (False, None)

    def _load(self):
        for key, row in self.rows.items():
            has, value = self._common_override(key)
            # Start from the queue's own value so an untouched row shows what the file will actually use
            row.set_value(value if has else self.queue_settings.get(key, self._fallback(key)))
            row.override.setChecked(has)

    @staticmethod
    def _fallback(key: str):
        return {'crf': 23, 'abr': 256}.get(key, False)

    def _clear_all(self):
        for row in self.rows.values():
            row.override.setChecked(False)
        self.accept()

    def _sync_grain(self):
        """Grain preservation depends on the encoder that will actually be used for this file"""
        row = self.rows.get('grain_mode')
        if row is None:
            return
        codec_row = self.rows.get('video_codec')
        codec = (codec_row.value() if codec_row and codec_row.override.isChecked()
                 else self.queue_settings.get('video_codec', 'libx265'))
        supported = codec in GRAIN_TUNE
        row.override.setEnabled(supported)
        row.editor.setEnabled(supported and row.override.isChecked())
        if not supported:
            row.override.setChecked(False)
            self.hint.setText(GRAIN_UNSUPPORTED_NOTE)
        else:
            self.hint.setText("")

    def overrides(self) -> Dict[str, Any]:
        """The overrides the dialog was left holding, ready for VideoFile.set_overrides"""
        return {key: row.value() for key, row in self.rows.items() if row.override.isChecked()}

    def apply_to_files(self):
        """
        Write the result back. With several files selected only the ticked rows are touched, so a row
        left unticked clears that key rather than silently keeping each file's old private value —
        otherwise 'follow the queue' would not be reachable for a mixed selection.
        """
        chosen = self.overrides()
        for file in self.files:
            kept = {k: v for k, v in file.overrides.items() if k not in self.rows}
            file.set_overrides({**kept, **chosen})
