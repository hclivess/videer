"""
Repair for videer

Damaged video comes in two kinds, and they want opposite treatments.

**The container is broken.** The recording stopped mid-write, the download died, the index never got flushed.
The picture data is fine; what is missing is the bookkeeping — the index, the duration, sane timestamps, a
clean end. Rewriting the container fixes it completely and without touching a single compressed frame, so the
result is bit-for-bit the same video, only playable and seekable again.

**The picture data itself is damaged.** Bit rot, a bad transfer, a failing drive. No amount of container
rewriting helps: the corrupt packets are copied through verbatim and the decoder chokes on them exactly as
before. The only thing that helps is decoding past the damage — the decoder conceals what it cannot decode —
and writing a fresh stream, which costs a re-encode and is not reversible.

So repair here is: measure, treat, measure again. Every file is decoded end to end and its errors counted
before anything is done, treated with the cheapest strategy that could work, and decoded again afterwards so
the fix can be *shown* to have worked rather than asserted. A repair that did not reduce the error count is
reported as such, and the original is never touched unless replacing it was asked for.

What this cannot do: an MP4 whose index (the `moov` atom) never got written cannot be opened by FFmpeg at all,
and no option changes that — the file needs a tool like untrunc and a healthy reference recorded by the same
device. That case is detected and named rather than silently failed.
"""

import os
import re
import subprocess
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QGroupBox, QHBoxLayout,
                               QHeaderView, QLabel, QMessageBox, QProgressBar, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout)

from utils import childproc
from utils.ffmpeg_utils import FFmpegCommandBuilder, find_ffmpeg, probe_media_info
from utils.file_utils import FileOperations
from modules.process_manager import format_duration, format_size

# Strategies, cheapest first. 'auto' is the one worth using: it tries the lossless fix, measures, and only
# spends a re-encode when the measurement says the lossless fix was not enough.
REPAIR_STRATEGIES = [
    ("Automatic — rebuild the container, re-encode only if that is not enough", "auto"),
    ("Rebuild container only — lossless, keeps every frame exactly as it is", "remux"),
    ("Re-encode — decode past the damage and write a clean stream (lossy)", "reencode"),
]
DEFAULT_REPAIR_STRATEGY = "auto"

# Suffix for the repaired copy. The original is never overwritten in place; replacing it is a separate,
# explicit choice that still leaves a .old backup behind.
REPAIRED_SUFFIX = ".repaired"

# FFmpeg says this, and only this, when an MP4's index never made it to disk.
_MOOV_MISSING = re.compile(r"moov atom not found", re.IGNORECASE)


def is_progress_line(line: str) -> bool:
    """FFmpeg's -progress output is key=value with no spaces in the key; everything else is a message"""
    return '=' in line and ' ' not in line.split('=', 1)[0]


def build_check_command(ffmpeg: str, filepath: str) -> List[str]:
    """
    Decode the whole file and let FFmpeg complain. -v error keeps everything but real problems out, so the
    number of message lines *is* the error count.
    """
    return [ffmpeg, '-hide_banner', '-v', 'error',
            '-err_detect', 'crccheck+bitstream+buffer',
            '-progress', 'pipe:1', '-nostats',
            '-i', filepath, '-f', 'null', '-']


def build_remux_command(ffmpeg: str, source: str, output: str) -> List[str]:
    """
    Rewrite the container, copying every stream untouched: a new index, regenerated timestamps, a clean end
    of file. Nothing here re-compresses anything, so a file whose only problem was its container comes out
    identical in content and fixed in form.
    """
    extension = os.path.splitext(output)[1].lower()
    cmd = [ffmpeg, '-hide_banner', '-progress', 'pipe:1', '-nostats',
           '-err_detect', 'ignore_err', '-fflags', '+genpts+discardcorrupt',
           '-i', source, '-y',
           '-map', '0', '-c', 'copy',
           '-avoid_negative_ts', 'make_zero']
    if extension in ('.mp4', '.m4v', '.mov'):
        cmd.extend(['-movflags', '+faststart'])
    cmd.append(output)
    return cmd


def build_reencode_command(settings: Dict[str, Any], source: str, output: str) -> List[str]:
    """
    A full re-encode with the settings on the Video and Audio tabs, with error tolerance forced on so the
    decoder works past the damage instead of stopping at it.
    """
    builder = FFmpegCommandBuilder({**settings, 'repair_mode': True, 'corrupt_fix': True})
    return builder.build_main_command(source, output)


def repaired_path(filepath: str, extension: Optional[str] = None) -> str:
    """<name>.repaired.<ext> next to the original"""
    base, original_ext = os.path.splitext(filepath)
    return base + REPAIRED_SUFFIX + (extension or original_ext)


class RepairWorker(QThread):
    """
    Checks and repairs a list of files on a worker thread.

    Modelled on the quality search rather than on the encoding queue: this is a job the user starts from a
    dialog, watches, and can stop, not something the batch runs on its way past.
    """

    file_started = Signal(int, str)         # row, filename
    file_progress = Signal(int, str)        # row, what is happening now
    file_finished = Signal(int, dict)       # row, report
    all_finished = Signal(list)             # every report

    def __init__(self, files: List[str], settings: Dict[str, Any], strategy: str,
                 replace_originals: bool = False, check_only: bool = False, parent=None):
        super().__init__(parent)
        self.files = list(files)
        self.settings = dict(settings)
        self.strategy = strategy
        self.replace_originals = replace_originals
        self.check_only = check_only

        self.should_stop = False
        self._proc: Optional[subprocess.Popen] = None
        self.reports: List[Dict[str, Any]] = []
        self.file_ops = FileOperations()

    # ------------------------------------------------------------------
    def stop(self):
        self.should_stop = True
        proc = self._proc
        if proc is not None:
            childproc.kill(proc)

    def run(self):
        ffmpeg = find_ffmpeg()
        for row, filepath in enumerate(self.files):
            if self.should_stop:
                break
            self.file_started.emit(row, os.path.basename(filepath))
            if not ffmpeg:
                report = self._report(filepath, status='failed', note="FFmpeg was not found.")
            else:
                try:
                    report = self._handle(row, ffmpeg, filepath)
                except Exception as exc:                  # noqa: BLE001 - one bad file must not end the run
                    report = self._report(filepath, status='failed',
                                          note=f"{type(exc).__name__}: {exc}")
            self.reports.append(report)
            self.file_finished.emit(row, report)
        self.all_finished.emit(self.reports)

    # ------------------------------------------------------------------
    def _report(self, filepath: str, **fields) -> Dict[str, Any]:
        report = {
            'path': filepath,
            'name': os.path.basename(filepath),
            'opened': True,
            'errors_before': None,
            'errors_after': None,
            'duration_before': None,
            'duration_after': None,
            'frames_before': None,
            'frames_after': None,
            'strategy': None,
            'output': None,
            'size': None,
            'status': 'ok',
            'note': '',
        }
        report.update(fields)
        return report

    def _handle(self, row: int, ffmpeg: str, filepath: str) -> Dict[str, Any]:
        self.file_progress.emit(row, "checking…")
        before = self.check(ffmpeg, filepath)
        info = probe_media_info(filepath)

        if not before['opened']:
            # Nothing can be repaired that cannot be read. Say which wall we hit.
            note = ("The MP4 index (moov atom) is missing — the file was cut off before it was written. "
                    "FFmpeg cannot open this at all; recovering it needs a tool like untrunc together with "
                    "an undamaged file recorded by the same device."
                    if before['moov_missing'] else
                    "FFmpeg could not open this file: " + (before['first_error'] or "unknown reason"))
            return self._report(filepath, opened=False, status='unrepairable', note=note,
                                errors_before=before['errors'])

        if self.check_only:
            # Nothing is written, nothing is touched — this run only answers "is anything wrong".
            return self._report(
                filepath,
                status='clean' if before['errors'] == 0 else 'damaged',
                errors_before=before['errors'], frames_before=before['frames'],
                duration_before=info.get('duration'),
                note=("No errors found." if before['errors'] == 0 else
                      f"{before['errors']} error(s) while decoding. "
                      + (before['first_error'] or '')[:160]))

        if before['errors'] == 0:
            return self._report(filepath, status='clean', errors_before=0, errors_after=0,
                                duration_before=info.get('duration'), frames_before=before['frames'],
                                frames_after=before['frames'],
                                note="No errors found — nothing to repair.")

        attempts = {'auto': ['remux', 'reencode'], 'remux': ['remux'], 'reencode': ['reencode']}
        best: Optional[Dict[str, Any]] = None

        for strategy in attempts.get(self.strategy, ['remux']):
            if self.should_stop:
                break
            attempt = self._attempt(row, ffmpeg, filepath, strategy, before['errors'])
            if attempt is None:
                continue
            if best is None or attempt['errors_after'] < best['errors_after']:
                if best is not None:
                    self._discard(best['output'])
                best = attempt
            elif attempt is not best:
                self._discard(attempt['output'])
            if best['errors_after'] == 0:
                break                                     # cheapest strategy that clears it wins

        if best is None:
            return self._report(filepath, status='failed', errors_before=before['errors'],
                                duration_before=info.get('duration'), frames_before=before['frames'],
                                note="Every repair attempt failed to produce a usable file.")

        output = best['output']
        note = ""
        recovered = self._recovery_note(before, best, info)
        if best['errors_after'] == 0:
            status = 'repaired'
        elif best['errors_after'] < before['errors']:
            status = 'improved'
            note = (f"{before['errors'] - best['errors_after']} of {before['errors']} errors are gone, "
                    f"{best['errors_after']} remain.")
        else:
            status = 'unchanged'
            note = ("The repair did not reduce the error count — the damage is in the compressed picture "
                    "data itself. The file was kept anyway so it can be compared.")

        if recovered:
            note = (note + " " if note else "") + recovered
        if self.replace_originals and status in ('repaired', 'improved'):
            self.file_progress.emit(row, "replacing the original…")
            replaced, replace_note = self._replace_original(output, filepath)
            if replaced:
                output = replaced
                note = (note + " " if note else "") + replace_note

        return self._report(
            filepath, status=status, note=note,
            errors_before=before['errors'], errors_after=best['errors_after'],
            duration_before=info.get('duration'), duration_after=best['duration_after'],
            frames_before=before['frames'], frames_after=best['frames_after'],
            strategy=best['strategy'], output=output,
            size=os.path.getsize(output) if os.path.exists(output) else None)

    def _attempt(self, row: int, ffmpeg: str, filepath: str, strategy: str,
                 errors_before: int) -> Optional[Dict[str, Any]]:
        """Run one strategy and measure what it achieved. None if it produced nothing usable."""
        if strategy == 'remux':
            output = repaired_path(filepath)
            command = build_remux_command(ffmpeg, filepath, output)
            self.file_progress.emit(row, "rebuilding the container…")
        else:
            extension = '.' + (self.settings.get('output_format') or 'mkv').lower()
            output = repaired_path(filepath, extension)
            if os.path.normcase(os.path.abspath(output)) == os.path.normcase(os.path.abspath(filepath)):
                output = repaired_path(filepath, '.mkv')
            command = build_reencode_command(self.settings, filepath, output)
            self.file_progress.emit(row, "re-encoding past the damage…")

        self._discard(output)
        self._run(command)
        if self.should_stop:
            self._discard(output)
            return None
        if not os.path.isfile(output) or os.path.getsize(output) == 0:
            self._discard(output)
            return None

        self.file_progress.emit(row, "checking the result…")
        after = self.check(ffmpeg, output)
        if not after['opened']:
            self._discard(output)
            return None
        return {'strategy': strategy, 'output': output, 'errors_after': after['errors'],
                'frames_after': after['frames'],
                'duration_after': (probe_media_info(output) or {}).get('duration')}

    def _replace_original(self, repaired: str, original: str) -> tuple:
        """
        Put the repaired file where the original was, keeping the original as .old.

        The container decides the name — a re-encode can land in MKV where the source was MP4 — and that is
        FileOperations' business, not this module's.
        """
        target = self.file_ops.replace_file_as(repaired, original, None, keep_backup=True)
        if target is None:
            return None, (f"Could not replace the original; the repair is at "
                          f"{os.path.basename(repaired)}.")
        if os.path.splitext(target)[1].lower() == os.path.splitext(original)[1].lower():
            return target, "Original kept as .old next to it."
        return target, (f"Original kept as {os.path.basename(original)}.old"
                        f"{os.path.splitext(original)[1]}; the repair is "
                        f"{os.path.splitext(target)[1]}, so it kept its own extension.")

    @staticmethod
    def _recovery_note(before: Dict[str, Any], best: Dict[str, Any],
                       info: Dict[str, Any]) -> str:
        """
        What survived. For a file that was cut off this is the number that matters: the container said one
        duration and the frames say another, and after the rebuild both agree on what is really there.
        """
        frames_before, frames_after = before.get('frames'), best.get('frames_after')
        duration_before, duration_after = info.get('duration'), best.get('duration_after')
        if frames_before and frames_after and abs(frames_after - frames_before) > max(2, frames_before * 0.01):
            return f"{frames_after} of {frames_before} frames came through."
        if duration_before and duration_after and abs(duration_after - duration_before) > 1.0:
            return (f"Runs {format_duration(duration_after)}, where the damaged file claimed "
                    f"{format_duration(duration_before)}.")
        return ""

    @staticmethod
    def _discard(path: Optional[str]):
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass

    # ------------------------------------------------------------------
    def check(self, ffmpeg: str, filepath: str) -> Dict[str, Any]:
        """
        Decode the file and count what FFmpeg complains about.

        Whether the file *opened* is read from the progress stream, not from the error text: "Invalid data
        found when processing input" is what FFmpeg says both when it cannot open a file at all and when it
        trips over a damaged packet halfway through a file it opened perfectly well. Matching on that wording
        declares every bit-rotten file unopenable — which is precisely the case repair exists for. A file
        that opened emits at least one progress block; one that did not emits none, and that is unambiguous.
        """
        messages: List[str] = []
        state = {'progress': 0, 'frames': None}

        def on_line(line: str):
            if is_progress_line(line):
                key, _, value = line.partition('=')
                if key == 'progress':
                    state['progress'] += 1
                elif key == 'frame':
                    state['frames'] = value.strip()
                return
            messages.append(line)

        self._run(build_check_command(ffmpeg, filepath), on_line)

        frames = None
        try:
            frames = int(state['frames']) if state['frames'] is not None else None
        except ValueError:
            pass

        return {
            'errors': len(messages),
            'opened': state['progress'] > 0,
            'frames': frames,
            'moov_missing': any(_MOOV_MISSING.search(line) for line in messages),
            'first_error': messages[0] if messages else None,
            'messages': messages[:20],
        }

    def _run(self, command: List[str], on_line: Optional[Callable[[str], None]] = None) -> int:
        try:
            proc = childproc.popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   stdin=subprocess.DEVNULL, universal_newlines=True,
                                   encoding='utf-8', errors='replace', bufsize=1)
        except OSError:
            return 1

        self._proc = proc
        try:
            for raw_line in proc.stdout:
                if self.should_stop:
                    childproc.kill(proc)
                    return 1
                line = raw_line.strip()
                if line and on_line:
                    on_line(line)
            return proc.wait()
        finally:
            childproc.release(proc)
            self._proc = None


# Colour and wording for each outcome, so a table of thirty files can be read at a glance
STATUS_STYLE = {
    'clean':        ("no errors", Qt.GlobalColor.darkGreen),
    'damaged':      ("damaged", Qt.GlobalColor.darkRed),
    'repaired':     ("repaired", Qt.GlobalColor.darkGreen),
    'improved':     ("partly repaired", Qt.GlobalColor.darkYellow),
    'unchanged':    ("no improvement", Qt.GlobalColor.darkRed),
    'unrepairable': ("cannot be opened", Qt.GlobalColor.darkRed),
    'failed':       ("failed", Qt.GlobalColor.darkRed),
    'pending':      ("", Qt.GlobalColor.gray),
}

STRATEGY_NAMES = {'remux': "container rebuilt", 'reencode': "re-encoded", None: ""}


class RepairDialog(QDialog):
    """
    Check and repair the whole queue.

    A batch view on purpose: the answer to "which of these forty recordings is damaged, and can it be fixed"
    is a table, not forty separate runs. Checking is free of consequences and can be done on its own; repair
    writes a new file beside each original and never overwrites one unless that is explicitly asked for.
    """

    COLUMNS = ["File", "Errors", "After", "What was done", "Result", "Output"]

    def __init__(self, main_window, parent=None):
        super().__init__(parent or main_window)
        self.main_window = main_window
        self.setWindowTitle("Check & Repair Files")
        self.setMinimumWidth(860)

        self.worker: Optional[RepairWorker] = None
        self.files: List[str] = [f.filepath for f in main_window.file_manager.get_queue()]
        self._build_ui()
        self._fill_table()

    # ------------------------------------------------------------------
    def _build_ui(self):
        layout = QVBoxLayout(self)

        intro = QLabel(
            "Decodes every file in the queue from end to end and counts what FFmpeg complains about, then — "
            "if you ask it to — repairs each one and decodes it again, so the fix can be shown to have "
            "worked rather than assumed.")
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #555;")
        layout.addWidget(intro)

        options = QGroupBox("How to repair")
        options_layout = QVBoxLayout(options)

        strategy_row = QHBoxLayout()
        strategy_row.addWidget(QLabel("Strategy:"))
        self.strategy_combo = QComboBox()
        for label, key in REPAIR_STRATEGIES:
            self.strategy_combo.addItem(label, key)
        self.strategy_combo.setToolTip(
            "Rebuilding the container fixes a broken index, wrong timestamps or a file that was cut off\n"
            "mid-write, and costs nothing: every frame is copied through untouched.\n\n"
            "It cannot fix damage inside the compressed picture data — for that the video has to be decoded\n"
            "past the damage and written again, which means a re-encode and its quality loss.\n\n"
            "Automatic tries the free one first and only spends the re-encode if the error count says it "
            "must.")
        self.strategy_combo.currentIndexChanged.connect(self._update_hint)
        strategy_row.addWidget(self.strategy_combo, 1)
        options_layout.addLayout(strategy_row)

        self.hint_label = QLabel()
        self.hint_label.setWordWrap(True)
        self.hint_label.setStyleSheet("color: #666; font-size: 11px;")
        options_layout.addWidget(self.hint_label)

        self.replace_check = QCheckBox("Replace the original with the repaired file (keeps <name>.old)")
        self.replace_check.setStyleSheet("color: #d9534f;")
        self.replace_check.setToolTip(
            "Off by default: the repaired file is written as <name>.repaired.<ext> next to the original, so\n"
            "the two can be compared before anything is given up. Only files that actually improved are\n"
            "ever replaced.")
        options_layout.addWidget(self.replace_check)

        layout.addWidget(options)

        button_row = QHBoxLayout()
        self.check_button = QPushButton("Check only")
        self.check_button.setToolTip("Decode every file and report what is wrong. Writes nothing.")
        self.check_button.clicked.connect(lambda: self._start(check_only=True))
        self.repair_button = QPushButton("Check && Repair")
        self.repair_button.setStyleSheet(
            "QPushButton { font-weight: bold; padding: 6px 14px; background-color: #0d6efd; color: white;"
            " border-radius: 4px; } QPushButton:disabled { background-color: #cccccc; }")
        self.repair_button.clicked.connect(lambda: self._start(check_only=False))
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._on_cancel)
        button_row.addWidget(self.check_button)
        button_row.addWidget(self.repair_button)
        button_row.addWidget(self.cancel_button)
        button_row.addStretch()
        layout.addLayout(button_row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        layout.addWidget(self.progress_bar)

        self.status_label = QLabel("Ready.")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2, 3, 4):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setMinimumHeight(240)
        layout.addWidget(self.table)

        self.summary_label = QLabel()
        self.summary_label.setWordWrap(True)
        self.summary_label.setVisible(False)
        layout.addWidget(self.summary_label)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._update_hint()

    def _update_hint(self):
        settings = self.main_window.ui_manager.get_current_settings()
        if self.strategy_combo.currentData() == 'remux':
            self.hint_label.setText("Nothing is re-compressed, so this cannot change how the video looks — "
                                    "and cannot fix damage in the picture data either.")
        else:
            text = (f"Re-encoding uses the current Video and Audio settings: {settings.get('video_codec')} at "
                    f"CRF {settings.get('crf')}, {settings.get('audio_codec')}, into "
                    f"{settings.get('output_format')}. Change them on the tabs behind this dialog if that is "
                    f"not what the repaired file should be.")
            if settings.get('audio_codec') == 'copy':
                # Worth saying plainly: a re-encode that copies the audio fixes the picture and leaves every
                # damaged audio packet exactly where it was, which reads as a repair that half worked.
                text += (" Audio is set to copy, so damaged audio will be carried through untouched — "
                         "choose a real audio codec to have it rebuilt too.")
            if settings.get('video_codec') == 'copy':
                text += (" Video is set to copy, so this cannot repair the picture at all; it would only "
                         "rebuild the container.")
            self.hint_label.setText(text)

    # ------------------------------------------------------------------
    def _fill_table(self):
        self.table.setRowCount(len(self.files))
        for row, filepath in enumerate(self.files):
            self._set_row(row, [os.path.basename(filepath), "", "", "", "", ""])
            self.table.item(row, 0).setToolTip(filepath)

    def _set_row(self, row: int, values: List[str]):
        for column, text in enumerate(values):
            item = QTableWidgetItem(text)
            if column:
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, column, item)

    # ------------------------------------------------------------------
    def _start(self, check_only: bool):
        if self.main_window.process_manager.is_processing():
            QMessageBox.information(self, "Busy",
                                    "The queue is encoding. Repair reads and writes whole files; run it "
                                    "before starting the queue, or after it finishes.")
            return
        if not self.files:
            QMessageBox.warning(self, "No Files",
                                "The queue is empty. Add the files you want checked, then reopen this.")
            return
        if not find_ffmpeg():
            QMessageBox.warning(self, "FFmpeg missing", "FFmpeg was not found.")
            return

        missing = [f for f in self.files if not os.path.isfile(f)]
        if missing:
            QMessageBox.warning(self, "Files missing",
                                f"{len(missing)} file(s) are no longer where the queue expects them:\n"
                                + "\n".join(os.path.basename(f) for f in missing[:5]))
            return

        self._fill_table()
        self.summary_label.setVisible(False)
        self.progress_bar.setValue(0)
        self._set_running(True)

        self.worker = RepairWorker(
            self.files, self.main_window.ui_manager.get_current_settings(),
            strategy=self.strategy_combo.currentData(),
            replace_originals=self.replace_check.isChecked() and not check_only,
            check_only=check_only, parent=self)
        self.worker.file_started.connect(self._on_file_started)
        self.worker.file_progress.connect(self._on_file_progress)
        self.worker.file_finished.connect(self._on_file_finished)
        self.worker.all_finished.connect(self._on_all_finished)
        self.worker.finished.connect(lambda: self._set_running(False))
        self.worker.start()

    def _set_running(self, running: bool):
        self.check_button.setEnabled(not running)
        self.repair_button.setEnabled(not running)
        self.cancel_button.setEnabled(running)
        self.strategy_combo.setEnabled(not running)
        self.replace_check.setEnabled(not running)

    def _on_cancel(self):
        if self.worker and self.worker.isRunning():
            self.status_label.setText("Stopping…")
            self.worker.stop()

    def _on_file_started(self, row: int, name: str):
        self.status_label.setText(f"[{row + 1}/{len(self.files)}] {name}")
        self.table.setCurrentCell(row, 0)

    def _on_file_progress(self, row: int, message: str):
        self.table.item(row, 4).setText(message)
        self.status_label.setText(f"[{row + 1}/{len(self.files)}] "
                                  f"{os.path.basename(self.files[row])} — {message}")

    def _on_file_finished(self, row: int, report: Dict[str, Any]):
        label, colour = STATUS_STYLE.get(report['status'], (report['status'], Qt.GlobalColor.black))
        after = report['errors_after']
        self._set_row(row, [
            report['name'],
            "--" if report['errors_before'] is None else str(report['errors_before']),
            "--" if after is None else str(after),
            STRATEGY_NAMES.get(report['strategy'], report['strategy'] or ""),
            label,
            (f"{os.path.basename(report['output'])} ({format_size(report['size'])})"
             if report['output'] and report['size'] else
             os.path.basename(report['output']) if report['output'] else ""),
        ])
        self.table.item(row, 4).setForeground(colour)
        if report['note']:
            for column in range(len(self.COLUMNS)):
                self.table.item(row, column).setToolTip(report['note'])
        self.progress_bar.setValue(int((row + 1) * 100 / max(1, len(self.files))))

    def _on_all_finished(self, reports: List[Dict[str, Any]]):
        counts: Dict[str, int] = {}
        for report in reports:
            counts[report['status']] = counts.get(report['status'], 0) + 1

        parts = [f"{count} {STATUS_STYLE.get(status, (status, None))[0]}"
                 for status, count in sorted(counts.items())]
        stopped = " (stopped early)" if self.worker and self.worker.should_stop else ""
        self.summary_label.setText(
            f"{len(reports)} of {len(self.files)} file(s) processed{stopped}: " + ", ".join(parts)
            + ("\nHover a row for the details of what was found."
               if any(r['note'] for r in reports) else ""))
        self.summary_label.setStyleSheet("font-weight: bold;")
        self.summary_label.setVisible(True)
        self.status_label.setText("Done.")
        self.progress_bar.setValue(100)
        self.main_window.ui_manager.update_status(
            "Repair: " + ", ".join(parts) if parts else "Repair finished")

    # ------------------------------------------------------------------
    def done(self, code):
        self._shutdown()
        super().done(code)

    def closeEvent(self, event):
        self._shutdown()
        super().closeEvent(event)

    def _shutdown(self):
        """A repair pass must never outlive the window that started it"""
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait(10000)
