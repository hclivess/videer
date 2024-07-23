import os
from utils import get_logger

class File:
    def __init__(self, number, filename):
        self.number = number
        self.filename = filename
        self.basename = os.path.splitext(self.filename)[0]
        self.extension = ".mkv"
        self.ffmpeg_errors = []

    @property
    def orig_name(self):
        return self.filename

    @property
    def transcodename(self):
        return f"{self.basename}.trans.avi"

    @property
    def errorname(self):
        return f"{self.filename}.error"

    @property
    def ffindex(self):
        return f"{self.filename}.ffindex"

    @property
    def tempffindex(self):
        return f"{self.transcodename}.ffindex"

    @property
    def displayname(self):
        return os.path.basename(self.filename)

    @property
    def outputname(self):
        return f"{self.basename}_{app.crf.get()}{app.codec_var.get()}_{app.audio_codec_var.get()}{app.abr.get()}{self.extension}"

    @property
    def dir(self):
        return os.path.dirname(os.path.realpath(self.filename))

    @property
    def avsfile(self):
        return f"{self.basename}.avs"

    def create_logger(self):
        self.log = get_logger(self.filename)