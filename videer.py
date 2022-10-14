import threading
from tkinter import *
from tkinter.ttk import *

from ttkwidgets import ScaleEntry
import tkinter as tk

import tkinter.messagebox as messagebox
import tkinter.filedialog as fd
import tkinter.scrolledtext as st
import logging.handlers
import subprocess
import multiprocessing
import psutil
from playsound import playsound
import os

def assemble(input, output, app_gui, transcode_source):
    command = [f'ffmpeg.exe -hide_banner']
    if app_gui.use_avisynth_var.get():
        command.append(f'-i "parameters.avs" -y')
        CreateAvs(infile=input)
    else:
        command.append(f'-i "{input}" -y')

    command.append(f'-c:v {app_gui.codec_var.get()}')
    command.append(f'-preset {app_gui.preset_get(int(app_gui.speed.get()))}')
    command.append(f'-map 0:v -map 0:a -map 0:s?')
    command.append(f'-crf {app_gui.crf.get()}')
    command.append(f'-c:a {app_gui.audio_codec_var.get()}')
    command.append(f'-b:a {app_gui.abr.get()}k')
    command.append(f'-c:s copy')

    if int(app_gui.corrupt_var.get()) == 1 and not transcode_source:
        command.append("-bsf:v h264_mp4toannexb")

    command.append(f'{app_gui.extras_value.get()}')
    command.append('-metadata comment="Made with Videer https://github.com/hclivess/videer"')
    command.append(f'-metadata description="'
                   f'Video Codec: {app_gui.codec_var.get()}, '
                   f'Preset: {app_gui.preset_get(int(app_gui.speed.get()))}, '
                   f'CRF: {app_gui.crf.get()}, '
                   f'Audio Codec: {app_gui.audio_codec_var.get()}, '
                   f'Audio Bitrate: {app_gui.abr.get()}k"')
    command.append('-movflags')
    command.append('+faststart')
    command.append('-bf 2')
    command.append('-flags')
    command.append('+cgop')
    command.append('-pix_fmt yuv420p')
    command.append(f'-f matroska "{output}"')
    return " ".join(command)


class FileHandler:
    def __init__(self, file):
        self.number = file[0]
        self.filename = file[1]  # ..file.avi
        self.basename = os.path.splitext(self.filename)[0]  # ..file
        self.extras = None  # .._x265_..
        self.extension = ".mkv"  # .mkv
        self.tempname = f"{self.basename}.temp.avi"  # ..file.temp.avi
        # self.transcodemame = f"{self.tempname}{self.extension}"  # ..file.temp.avi.mkv
        self.errorname = f"{self.filename}.error"  # ..file.avi.error
        self.ffindex = f"{self.filename}.ffindex"  # ..file.avi.ffindex
        self.tempffindex = f"{self.tempname}.ffindex"  # ..file.avi.ffindex
        self.displayname = self.filename.split('/')[-1]  # file.avi
        self.outputname = f"{self.basename}_{app.crf.get()}{app.codec_var.get()}_{app.audio_codec_var.get()}{app.abr.get()}{self.extension}"


class CreateAvs:
    def __init__(self, infile):
        with open("parameters.avs", "w") as avsfile:
            avsfile.write('Loadplugin("plugins/masktools2.dll")')
            avsfile.write('\n')
            avsfile.write('Loadplugin("plugins/mvtools2.dll")')
            avsfile.write('\n')
            avsfile.write('Loadplugin("plugins/nnedi3.dll")')
            avsfile.write('\n')
            avsfile.write('Loadplugin("plugins/ffms2.dll")')
            avsfile.write('\n')
            avsfile.write('Loadplugin("plugins/RgTools.dll")')
            avsfile.write('\n')
            avsfile.write('Import("plugins/QTGMC.avsi")')
            avsfile.write('\n')
            avsfile.write('Import("plugins/Zs_RF_Shared.avsi")')
            avsfile.write('\n')

            if app.use_ffms2_var.get():
                avsfile.write(f'FFmpegSource2("{infile}", vtrack = -1, atrack = -1)')
            else:
                avsfile.write(f'AVISource("{infile}", audio=true)')
            avsfile.write('\n')

            avsfile.write('SetFilterMTMode("FFVideoSource", 3)')
            avsfile.write('\n')
            avsfile.write('ConvertToYV24(matrix="rec709")')
            avsfile.write('\n')
            avsfile.write(f'Prefetch({multiprocessing.cpu_count()})')
            avsfile.write('\n')

            if app.avisynth_extras.get("1.0", END).strip():
                avsfile.write(app.avisynth_extras.get("1.0", END))
                avsfile.write('\n')

            if app.deinterlace_var.get() and not app.tff_var.get():
                avsfile.write(f'QTGMC(Preset="{app.preset_get(int(app.speed.get()))}", EdiThreads={multiprocessing.cpu_count()})')
                avsfile.write('\n')

            elif app.deinterlace_var.get() and app.tff_var.get():
                avsfile.write(f'QTGMC(Preset="{app.preset_get(int(app.speed.get()))}", EdiThreads={multiprocessing.cpu_count()})')
                avsfile.write('DoubleWeave().SelectOdd()')
                avsfile.write('QTGMC(InputType=2)')
                avsfile.write('\n')


class Application(Frame):
    def __init__(self, master=None):

        super().__init__(master)
        self.master = master

        self.grid()
        self.create_widgets()
        self.file_queue = []
        self.tempfile = None
        self.should_transcode = False
        self.process = None

    def transcode(self, file, transcode_video, transcode_audio):

        temp_transcode = None

        if transcode_video and transcode_audio:
            temp_transcode = f'ffmpeg.exe -hide_banner -i "{file.filename}" -preset {self.preset_get(int(self.speed.get()))} -map 0:v -map 0:a -map 0:s? -c:a pcm_s32le -c:v rawvideo -c:s copy -hide_banner "{file.tempname}" -y'
        elif transcode_video and not transcode_audio:
            temp_transcode = f'ffmpeg.exe -hide_banner -i "{file.filename}" -preset {self.preset_get(int(self.speed.get()))} -map 0:v -map 0:a -map 0:s? -c:a copy -c:v rawvideo -c:s copy -hide_banner "{file.tempname}" -y'
        elif transcode_audio and not transcode_video:
            temp_transcode = f'ffmpeg.exe -hide_banner -i "{file.filename}" -preset {self.preset_get(int(self.speed.get()))} -map 0:v -map 0:a -map 0:s? -c:a pcm_s32le -c:v copy -c:s copy -hide_banner "{file.tempname}" -y'

        self.open_process(temp_transcode)

    def open_process(self, command_line):
        rootLogger.info("Process starting")
        self.process = subprocess.Popen(command_line)
        # process = subprocess.Popen(command_line, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        # stdout, stderr = process.communicate()
        outs, errs = self.process.communicate()
        return_code = self.process.returncode
        rootLogger.info(f"Return code: {return_code}")
        self.process.wait()
        return return_code

    def queue(self, files, info_box):
        """automatically ends on Popen termination"""

        for file in enumerate(files):

            fileobj = FileHandler(file=file)

            info_box.configure(state='normal')
            info_box.insert(END, f"Processing {fileobj.number + 1}/{len(files)}: "
                                    f"{fileobj.displayname}\n"
                            )
            info_box.configure(state='disabled')
            rootLogger.info(f"Processing {fileobj.displayname}")

            should_transcode_video = False
            should_transcode_audio = False
            if int(self.transcode_video_var.get()) == 1:
                should_transcode_video = True
            if int(self.transcode_audio_var.get()) == 1:
                should_transcode_audio = True
            if should_transcode_video or should_transcode_audio:
                self.should_transcode = True

            if self.should_transcode:
                self.transcode(fileobj,
                               transcode_video=should_transcode_video,
                               transcode_audio=should_transcode_audio)
                command_line = assemble(fileobj.tempname, fileobj.outputname, self, True)

            else:
                command_line = assemble(fileobj.filename, fileobj.outputname, self, False)

            return_code = self.open_process(command_line)

            if info_box:
                info_box.configure(state='normal')
                if return_code == 0:
                    """error code can be None, force numeric check"""
                    info_box.insert(END, f"Finished {fileobj.displayname}: "
                                            f"{int((fileobj.number + 1) / (len(files)) * 100)}% \n")
                    rootLogger.info(f"Finished {fileobj.displayname}")
                else:
                    info_box.insert(END, f"Error with {fileobj.displayname}: "
                                            f"{int((fileobj.number + 1) / (len(files)) * 100)}% \n")
                    rootLogger.info(f"Error with {fileobj.displayname}")

                    if os.path.exists(fileobj.outputname):
                        os.rename(fileobj.outputname, fileobj.errorname)

                info_box.configure(state='disabled')

            if int(self.replace_button_var.get()) == 1 and return_code == 0:
                self.replace_file(rename_from=fileobj.outputname,
                                  rename_to=fileobj.filename)

            if os.path.exists(fileobj.tempname):
                os.remove(fileobj.tempname)

            if os.path.exists(fileobj.ffindex):
                os.remove(fileobj.ffindex)

            if os.path.exists(fileobj.tempffindex):
                os.remove(fileobj.tempffindex)

        info_box.configure(state='normal')
        info_box.insert(END, "Queue finished")
        info_box.configure(state='disabled')
        rootLogger.info("Queue finished")

        try:
            playsound("done.mp3")
        except Exception as e:
            print("Failed to play sound")

    def create_info_box(self):
        self.top = Toplevel()
        self.top.title("Queue Info")
        self.top.resizable(False, False)

        info_box = st.ScrolledText(self.top, width=100, font=('calibri', 10, 'bold'))
        info_box.grid(row=0, pady=0)
        info_box.configure(state='disabled')
        return info_box

    def run_cmd(self):
        info_box = self.create_info_box()
        file_thread = threading.Thread(target=self.queue, args=(self.file_queue, info_box,))
        file_thread.start()

    def select_file(self, var):
        files = fd.askopenfilename(multiple=True, initialdir="", title="Select file")
        del self.file_queue[:]
        for file in files:
            self.file_queue.append(file)
        var.set(self.file_queue)

    def on_set_avisynth(self, click):
        """warning, reversed because it takes state at the time of clicking"""
        if self.use_avisynth_var.get():
            self.tff_var.set(False)
            self.use_ffms2_var.set(False)
            self.deinterlace_var.set(False)
    def on_set_deinterlace(self, click):
        """warning, reversed because it takes state at the time of clicking"""
        if not self.deinterlace_var.get() and not self.use_avisynth_var.get():
            self.use_avisynth_var.set(True)

        elif self.deinterlace_var.get() and self.use_avisynth_var.get() and not self.use_ffms2_var.get():
            self.use_avisynth_var.set(False)
            self.tff_var.set(False)
        elif self.deinterlace_var.get() and self.use_avisynth_var.get():
            self.tff_var.set(False)

    def on_set_ttf(self, click):
        """warning, reversed because it takes state at the time of clicking"""
        if not self.tff_var.get() and not self.deinterlace_var.get():
            self.deinterlace_var.set(True)
            self.use_avisynth_var.set(True)
        elif self.tff_var.get() and self.deinterlace_var.get() and not self.use_ffms2_var.get():
            self.deinterlace_var.set(False)
            self.use_avisynth_var.set(False)

    def on_set_ffms(self, click):
        """warning, reversed because it takes state at the time of clicking"""
        if not self.use_avisynth_var.get():
            self.use_avisynth_var.set(True)
        elif self.use_avisynth_var.get() and not self.deinterlace_var.get():
            self.use_avisynth_var.set(False)

    def exit(self):
        self.stop_process()
        self.master.destroy()

    def stop_process(self, announce=False):
        if self.process:
            output = f"Process terminated"
            self.process.kill()
        else:
            output = f"No process assigned"

        if announce:
            messagebox.showinfo(title="Info", message=output)

        if self.top:
            self.top.destroy()

    def preset_get(self, number: int):

        config_dict = {0: "veryslow", 1: "slower", 2: "slow", 3: "medium", 4: "faster", 5: "fast", 6: "ultrafast"}
        return config_dict.get(number)

    def replace_file(self, rename_from, rename_to):

        input_name_no_ext = os.path.splitext(rename_to)[0]
        new_name_ext = f"{input_name_no_ext}.mkv"

        if os.path.exists(new_name_ext) and rename_to != new_name_ext:
            rootLogger.info("File already exists, not replacing")
        else:
            rootLogger.info("Replacing original file as requested")
            if os.path.exists(new_name_ext):
                os.rename(new_name_ext, f"{new_name_ext}.old")
            os.rename(rename_from, new_name_ext)
            if rename_to != new_name_ext:
                os.rename(rename_to, f"{new_name_ext}.old")

    def create_widgets(self):

        self.deinterlace_var = BooleanVar()
        self.deinterlace_var.set(False)
        self.deinterlace_button = Checkbutton(self, text="Deinterlace", variable=self.deinterlace_var)
        self.deinterlace_button.bind("<Button-1>", self.on_set_deinterlace)
        self.deinterlace_button.grid(row=0, column=1, sticky='w', pady=0, padx=0)

        self.tff_var = BooleanVar()
        self.tff_var.set(False)
        self.tff_button = Checkbutton(self, text="Top Field First", variable=self.tff_var)
        self.tff_button.bind("<Button-1>", self.on_set_ttf)
        self.tff_button.grid(row=1, column=1, sticky='w', pady=0, padx=0)

        self.use_avisynth_var = BooleanVar()
        self.use_avisynth_var.set(False)
        self.use_avisynth_button = Checkbutton(self, text="Use AviSynth+", variable=self.use_avisynth_var)
        self.use_avisynth_button.bind("<Button-1>", self.on_set_avisynth)
        self.use_avisynth_button.grid(row=2, column=1, sticky='w', pady=0, padx=0)

        self.use_ffms2_var = BooleanVar()
        self.use_ffms2_var.set(False)
        self.use_ffms2_button = Checkbutton(self, text="Use ffms2 (no frameserver, 1 stream)",
                                               variable=self.use_ffms2_var)
        self.use_ffms2_button.bind("<Button-1>", self.on_set_ffms)
        self.use_ffms2_button.grid(row=3, column=1, sticky='w', pady=0, padx=0)

        self.transcode_video_var = BooleanVar()
        self.transcode_video_var.set(False)
        self.transcode_video_button = Checkbutton(self, text="Raw transcode video first",
                                                     variable=self.transcode_video_var)
        self.transcode_video_button.grid(row=4, column=1, sticky='w', pady=0, padx=0)

        self.transcode_audio_var = BooleanVar()
        self.transcode_audio_var.set(False)
        self.transcode_audio_button = Checkbutton(self, text="Raw transcode audio first",
                                                     variable=self.transcode_audio_var)
        self.transcode_audio_button.grid(row=5, column=1, sticky='w', pady=0, padx=0)

        self.corrupt_var = BooleanVar()
        self.corrupt_var.set(False)
        self.corrupt_var_button = Checkbutton(self, text="Fix AVC (ts) corruption during raw transcode",
                                                 variable=self.corrupt_var)
        self.corrupt_var_button.grid(row=6, column=1, sticky='w', pady=0, padx=0)

        self.audio_codec_label = Label(self)
        self.audio_codec_label["text"] = "Audio Codec: "
        self.audio_codec_var = StringVar()
        self.audio_codec_var.set("aac")
        self.audio_codec_label.grid(row=7, column=0, sticky='SE', pady=0, padx=0)

        self.audio_codec_button = Radiobutton(self, text="LAME MP3", variable=self.audio_codec_var,
                                                 value="libmp3lame")
        self.audio_codec_button.grid(row=7, column=1, sticky='w', pady=0, padx=0)
        self.audio_codec_button = Radiobutton(self, text="AAC", variable=self.audio_codec_var, value="aac")
        self.audio_codec_button.grid(row=8, column=1, sticky='w', pady=0, padx=0)
        self.audio_codec_button = Radiobutton(self, text="Opus", variable=self.audio_codec_var, value="libopus")
        self.audio_codec_button.grid(row=9, column=1, sticky='w', pady=0, padx=0)
        self.audio_codec_button = Radiobutton(self, text="PCM32 (raw)", variable=self.audio_codec_var,
                                                 value="pcm_s32le")
        self.audio_codec_button.grid(row=10, column=1, sticky='w', pady=0, padx=0)

        self.codec_label = Label(self)
        self.codec_label["text"] = "Video Codec: "
        self.codec_var = StringVar()
        self.codec_var.set("libx265")
        self.codec_label.grid(row=11, column=0, sticky='SE', pady=0, padx=0)

        self.video_codec_button = Radiobutton(self, text="x264", variable=self.codec_var, value="libx264")
        self.video_codec_button.grid(row=11, column=1, sticky='w', pady=0, padx=0)
        self.video_codec_button = Radiobutton(self, text="x265", variable=self.codec_var, value="libx265")
        self.video_codec_button.grid(row=12, column=1, sticky='w', pady=0, padx=0)
        self.video_codec_button = Radiobutton(self, text="V9", variable=self.codec_var, value="libvpx-vp9")
        self.video_codec_button.grid(row=13, column=1, sticky='w', pady=0, padx=0)
        self.video_codec_button = Radiobutton(self, text="raw", variable=self.codec_var, value="rawvideo")
        self.video_codec_button.grid(row=14, column=1, sticky='w', pady=0, padx=0)

        self.speed_label = Label(self)
        self.speed_label["text"] = "Encoding Speed: "
        self.speed_label.grid(row=15, column=0, sticky='SE', pady=0, padx=0)

        self.speed = tk.Scale(self, from_=0, to=6, orient=HORIZONTAL, sliderrelief=FLAT)
        self.speed.grid(row=15, column=1, sticky='WE', pady=0, padx=0)
        self.speed.set(3)

        self.infile_value = StringVar()
        self.infile_value.set("c:/test.avi")

        self.infile_button = Button(self, text="Input File(s):",
                                       command=lambda: self.select_file(self.infile_value))
        self.infile_button.grid(row=20, column=0, sticky='SE', padx=0, pady=(5))

        self.infile = Entry(self, textvariable=self.infile_value, width=70)
        self.infile.grid(row=20, column=1, sticky='W', padx=0)

        self.crf_label = Label(self)
        self.crf_label["text"] = "CRF: "
        self.crf_label.grid(row=21, column=0, sticky='SE', pady=0, padx=0)
        self.crf = tk.Scale(self, from_=0, to=51, orient=HORIZONTAL, sliderrelief=FLAT)
        self.crf.grid(row=21, column=1, sticky='WE', pady=0, padx=0)
        self.crf.set(23)

        self.abr_label = Label(self)
        self.abr_label["text"] = "Audio ABR: "
        self.abr_label.grid(row=22, column=0, sticky='SE', pady=0, padx=0)

        self.abr = tk.Scale(self, resolution=16, from_=0, to=384, orient=HORIZONTAL, sliderrelief=FLAT)
        self.abr.grid(row=22, column=1, sticky='WE', pady=0, padx=0)
        self.abr.set(256)

        self.extras_label = Label(self)
        self.extras_label["text"] = "FFmpeg Extras: "
        self.extras_label.grid(row=23, column=0, sticky='SE', padx=0)
        self.extras_value = StringVar()
        self.extras_value.set("")
        self.extras = Entry(self, textvariable=self.extras_value, width=70)
        self.extras.grid(row=23, column=1, sticky='W', pady=0, padx=0)

        self.avisynth_extras_label = Label(self)
        self.avisynth_extras_label["text"] = "AviSynth+ Extras: "
        self.avisynth_extras_label.grid(row=24, column=0, sticky='SE', padx=0)
        self.avisynth_extras = Text(self, height=2, width=30)
        self.avisynth_extras.grid(row=24, column=1, sticky='WE', pady=0, padx=0)

        self.replace_button_var = BooleanVar()
        self.replace_button_var.set(False)
        self.replace_button = Checkbutton(self, text="Replace Original File(s)",
                                             variable=self.replace_button_var)

        self.replace_button.grid(row=25, column=1, sticky='SE', pady=0, padx=0)

        self.run = Button(self, text="Run", style='W.TButton', command=lambda: self.run_cmd())
        self.run.grid(row=0, column=2, sticky='WE', padx=0)

        self.stop = Button(self, text="Stop", style='W.TButton', command=lambda: self.stop_process(True))
        self.stop.grid(row=1, column=2, sticky='WE', padx=0)

        self.quit = Button(self, text="Quit", style='W.TButton', command=self.exit)
        self.quit.grid(row=2, column=2, sticky='WE', padx=0, pady=(0, 5))


if __name__ == "__main__":
    root = Tk()
    root.iconbitmap("icon.ico")
    style = Style()
    style.configure('W.TButton',
                    font=('calibri', 10, 'bold'),
                    foreground='black')

    root.wm_title("videer")
    root.resizable(False, False)

    # operations = Operations()

    logFormatter = logging.Formatter("%(asctime)s [%(threadName)-12.12s] [%(levelname)-5.5s]  %(message)s")
    rootLogger = logging.getLogger()
    rootLogger.setLevel(logging.INFO)
    fileHandler = logging.FileHandler(f"log.log")
    fileHandler.setFormatter(logFormatter)
    rootLogger.addHandler(fileHandler)
    consoleHandler = logging.StreamHandler()
    consoleHandler.setFormatter(logFormatter)
    rootLogger.addHandler(consoleHandler)

    app = Application(master=root)
    app.mainloop()
