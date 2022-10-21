import threading
from tkinter import *
from tkinter.ttk import *
import time
import tkinter as tk

import tkinter.messagebox as messagebox
import tkinter.filedialog as fd
import tkinter.scrolledtext as st
import logging.handlers
import subprocess
import multiprocessing
import os
import re


def multiple_replace(string, rep_dict):
    pattern = re.compile("|".join([re.escape(k) for k in sorted(rep_dict, key=len, reverse=True)]), flags=re.DOTALL)
    return pattern.sub(lambda x: rep_dict[x.group(0)], string)


def assemble_final(fileobj, app_gui):
    command = [f'ffmpeg.exe -err_detect crccheck+bitstream+buffer -hide_banner']
    if app_gui.use_avisynth_var.get():
        command.append(f'-i "{fileobj.avsfile}" -y')
        CreateAvs(fileobj=fileobj)
    else:
        command.append(f'-i "{fileobj.filename}" -y')

    if app_gui.should_stabilize:
        command.append(f"-vf vidstabtransform=smoothing=30:zoom=5:input='transforms.trf'")

    command.append(f'-c:v {app_gui.codec_var.get()}')
    command.append(f'-preset {app_gui.preset_get(int(app_gui.speed.get()))}')
    command.append(f'-map 0:v -map 0:a? -map 0:s?')
    command.append(f'-crf {app_gui.crf.get()}')
    command.append(f'-c:a {app_gui.audio_codec_var.get()}')
    command.append(f'-b:a {app_gui.abr.get()}k')
    command.append(f'-c:s copy')

    if app_gui.corrupt_var.get():
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
    command.append(f'-f matroska "{fileobj.outputname}"')
    command.append('-y')
    return " ".join(command)


class File:
    def __init__(self, file):
        self.number = file[0]
        self.filename = file[1]  # ..file.avi
        self.basename = os.path.splitext(self.filename)[0]  # ..file
        self.extras = None  # .._x265_..
        self.extension = ".mkv"  # .mkv
        self.transcodename = f"{self.basename}.trans.avi"  # ..file.temp.avi
        self.errorname = f"{self.filename}.error"  # ..file.avi.error
        self.ffindex = f"{self.filename}.ffindex"  # ..file.avi.ffindex
        self.tempffindex = f"{self.transcodename}.ffindex"  # ..file.avi.ffindex
        self.displayname = self.filename.split('/')[-1]  # file.avi
        self.outputname = f"{self.basename}_{app.crf.get()}{app.codec_var.get()}_{app.audio_codec_var.get()}{app.abr.get()}{self.extension}"
        self.dir = os.path.dirname(os.path.realpath(self.filename))
        self.ffmpeg_errors = []
        self.avsfile = f"{self.basename}.avs"
        # self.trf = f"{self.dir}\\{time.time_ns()}.trf".replace("\\", "\\\\").replace(":", "\:")

    def create_logger(self):
        self.log = get_logger(self.filename)


class CreateAvs:
    def __init__(self, fileobj):
        with open(fileobj.avsfile, "w") as avsfile:
            avsfile.write(f'Loadplugin("{app.path}/plugins/masktools2.dll")')
            avsfile.write('\n')
            avsfile.write(f'Loadplugin("{app.path}/plugins/mvtools2.dll")')
            avsfile.write('\n')
            avsfile.write(f'Loadplugin("{app.path}/plugins/nnedi3.dll")')
            avsfile.write('\n')
            avsfile.write(f'Loadplugin("{app.path}/plugins/ffms2.dll")')
            avsfile.write('\n')
            avsfile.write(f'Loadplugin("{app.path}/plugins/RgTools.dll")')
            avsfile.write('\n')
            avsfile.write(f'Import("{app.path}/plugins/QTGMC.avsi")')
            avsfile.write('\n')
            avsfile.write(f'Import("{app.path}/plugins/Zs_RF_Shared.avsi")')
            avsfile.write('\n')

            if app.use_ffms2_var.get():
                avsfile.write(f'FFmpegSource2("{fileobj.filename}", vtrack = -1, atrack = -1)')
            else:
                avsfile.write(f'AVISource("{fileobj.filename}", audio=true)')
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

            if app.tff_var.get():
                avsfile.write('AssumeTFF()')
                avsfile.write('\n')

            if app.deinterlace_var.get():
                if app.reduce_fps_var.get():
                    avsfile.write(
                        f'QTGMC(Preset="{app.preset_get(int(app.speed.get()))}", FPSDivisor=2, EdiThreads={multiprocessing.cpu_count()})')
                    avsfile.write('\n')
                else:
                    avsfile.write(
                        f'QTGMC(Preset="{app.preset_get(int(app.speed.get()))}", EdiThreads={multiprocessing.cpu_count()})')
                    avsfile.write('\n')


def info_box_insert(info_box, message, log_message=None, logger=None):
    try:
        info_box.configure(state='normal')
        info_box.insert(END, f"{message}\n")
        info_box.configure(state='disabled')
    except Exception as e:
        print(f"Info window closed: {e}")

    if logger:
        logger.info(log_message)


class Application(Frame):
    def __init__(self, master=None):

        super().__init__(master)
        self.master = master
        self.top = None

        self.grid()
        self.create_widgets()
        self.file_queue = []
        self.tempfile = None
        self.should_transcode = False
        self.should_stabilize = False
        self.process = None
        self.workdir = None
        self.should_stop = False
        self.path = os.getcwd()

    def transcode(self, fileobj, transcode_video, transcode_audio):
        fileobj.log.info("Transcode process started")
        temp_transcode = None

        if transcode_video and transcode_audio:
            temp_transcode = f'ffmpeg.exe -err_detect crccheck+bitstream+buffer -hide_banner -i "{fileobj.filename}" -preset {self.preset_get(int(self.speed.get()))} -map 0:v -map 0:a? -map 0:s? -c:a pcm_s32le -c:v rawvideo -c:s copy "{fileobj.transcodename}" -y'
        elif transcode_video and not transcode_audio:
            temp_transcode = f'ffmpeg.exe -err_detect crccheck+bitstream+buffer -hide_banner -i "{fileobj.filename}" -preset {self.preset_get(int(self.speed.get()))} -map 0:v -map 0:a? -map 0:s? -c:a copy -c:v rawvideo -c:s copy "{fileobj.transcodename}" -y'
        elif transcode_audio and not transcode_video:
            temp_transcode = f'ffmpeg.exe -err_detect crccheck+bitstream+buffer -hide_banner -i "{fileobj.filename}" -preset {self.preset_get(int(self.speed.get()))} -map 0:v -map 0:a? -map 0:s? -c:a pcm_s32le -c:v copy -c:s copy "{fileobj.transcodename}" -y'

        self.open_process(temp_transcode, fileobj)

    def open_process(self, command_line, fileobj):
        fileobj.log.info(f"Executing command {command_line}")

        with subprocess.Popen(command_line,
                              stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT,
                              universal_newlines=True) as self.process:

            errors = ["error", "invalid"]
            for line in self.process.stdout:
                # print(line, end='')
                fileobj.log.info(line.strip())
                self.status_var.set(multiple_replace(line,
                                                     {"       ": " ",
                                                      "    ": " ",
                                                      "time=": "",
                                                      "bitrate=  ": "br:",
                                                      "speed": "rate",
                                                      "size=": "",
                                                      "frame": "f",
                                                      "=": ":",
                                                      "\n": ""}))
                for error in errors:
                    if error in line.lower():
                        fileobj.ffmpeg_errors.append(line.strip())

        return_code = self.process.wait()

        fileobj.log.info(f"Return code: {return_code}")
        return return_code

    def queue(self, files, info_box):
        """automatically ends on Popen termination"""

        for f in enumerate(files):
            if self.should_stop:
                self.should_stop = False
                return

            fileobj = File(file=f)

            if not self.should_stop:
                fileobj.create_logger()

                if self.workdir != fileobj.dir:
                    self.workdir = fileobj.dir
                    info_box_insert(info_box=info_box,
                                    message=f"Switching directory to: {self.workdir}",
                                    log_message=f"Switching directory to: {self.workdir}",
                                    logger=fileobj.log)

                info_box_insert(info_box=info_box,
                                message=f"Processing {fileobj.number + 1}/{len(files)}: {fileobj.displayname}",
                                log_message=f"Processing {fileobj.displayname}",
                                logger=fileobj.log)

                should_transcode_video = False
                should_transcode_audio = False

                if self.stabilize_var.get():
                    self.should_stabilize = True
                if self.transcode_video_var.get():
                    should_transcode_video = True
                if self.transcode_audio_var.get():
                    should_transcode_audio = True
                if should_transcode_video or should_transcode_audio:
                    self.should_transcode = True

                if self.should_transcode and not self.should_stop:
                    self.transcode(fileobj,
                                   transcode_video=should_transcode_video,
                                   transcode_audio=should_transcode_audio)

                if self.should_transcode:
                    """use transcoded file as source"""
                    fileobj.filename = fileobj.transcodename

                if self.should_stabilize:
                    """prepares transforms file"""
                    fileobj.log.info("Preparing transforms file for stabilization")
                    self.open_process(
                        f'ffmpeg -i "{fileobj.filename}" -vf vidstabdetect=shakiness=7 -f null -',
                        fileobj=fileobj)

                final_cmd = assemble_final(fileobj, self)
                return_code = self.open_process(final_cmd, fileobj)  # final go

            else:
                return_code = 1

            if return_code == 0 and not self.should_stop:
                """error code can be None, force numeric check"""
                info_box_insert(info_box=info_box,
                                message=f"Finished {fileobj.displayname}: {int((fileobj.number + 1) / (len(files)) * 100)}%",
                                log_message=f"Finished {fileobj.displayname}",
                                logger=fileobj.log)
            elif not self.should_stop:
                info_box_insert(info_box=info_box,
                                message=f"Error with {fileobj.displayname}: {int((fileobj.number + 1) / (len(files)) * 100)}%",
                                log_message=f"Error with {fileobj.displayname}",
                                logger=fileobj.log)

                if os.path.exists(fileobj.outputname) and not self.should_stop:
                    os.replace(fileobj.outputname, fileobj.errorname)

            if fileobj.ffmpeg_errors:
                info_box_insert(info_box=info_box,
                                message="Errors:\n" + '\n'.join(fileobj.ffmpeg_errors),
                                log_message="Errors:\n " + '\n'.join(fileobj.ffmpeg_errors),
                                logger=fileobj.log)

            if self.replace_button_var.get() and return_code == 0 and not self.should_stop:
                self.replace_file(rename_from=fileobj.outputname,
                                  rename_to=fileobj.filename,
                                  log=fileobj.log)

            if os.path.exists(fileobj.transcodename) and not self.should_stop:
                os.remove(fileobj.transcodename)

            if os.path.exists(fileobj.ffindex) and not self.should_stop:
                os.remove(fileobj.ffindex)

            if os.path.exists(fileobj.tempffindex) and not self.should_stop:
                os.remove(fileobj.tempffindex)

            if os.path.exists(fileobj.avsfile) and not self.should_stop:
                os.remove(fileobj.avsfile)

            handlers = fileobj.log.handlers[:]
            for handler in handlers:
                fileobj.log.removeHandler(handler)
                handler.close()

        if self.should_stop:
            info_box_insert(info_box=info_box,
                            message="Queue stopped")
        else:
            info_box_insert(info_box=info_box,
                            message="Queue finished")

        self.should_stop = False



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
            self.reduce_fps_var.set(False)
            self.tff_var.set(False)
        elif self.deinterlace_var.get() and self.use_avisynth_var.get():
            self.tff_var.set(False)

    def on_set_ttf(self, click):
        """warning, reversed because it takes state at the time of clicking"""
        if not self.tff_var.get() and not self.deinterlace_var.get():
            self.deinterlace_var.set(True)
            self.use_avisynth_var.set(True)

    def on_set_reduce(self, click):
        """warning, reversed because it takes state at the time of clicking"""
        if not self.reduce_fps_var.get() and not self.deinterlace_var.get():
            self.deinterlace_var.set(True)
            self.use_avisynth_var.set(True)

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
        self.should_stop = True
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

    def replace_file(self, rename_from, rename_to, log):

        input_name_no_ext = os.path.splitext(rename_to)[0]
        new_name_ext = f"{input_name_no_ext}.mkv"

        if os.path.exists(new_name_ext) and rename_to != new_name_ext:
            log.info("File already exists, not replacing")
        else:
            log.info("Replacing original file as requested")
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

        self.reduce_fps_var = BooleanVar()
        self.reduce_fps_var.set(False)
        self.reduce_fps_button = Checkbutton(self, text="Reduce Frames", variable=self.reduce_fps_var)
        self.reduce_fps_button.bind("<Button-1>", self.on_set_reduce)
        self.reduce_fps_button.grid(row=2, column=1, sticky='w', pady=0, padx=0)

        self.use_avisynth_var = BooleanVar()
        self.use_avisynth_var.set(False)
        self.use_avisynth_button = Checkbutton(self, text="Use AviSynth+", variable=self.use_avisynth_var)
        self.use_avisynth_button.bind("<Button-1>", self.on_set_avisynth)
        self.use_avisynth_button.grid(row=3, column=1, sticky='w', pady=0, padx=0)

        self.use_ffms2_var = BooleanVar()
        self.use_ffms2_var.set(False)
        self.use_ffms2_button = Checkbutton(self, text="Use ffms2 (no frameserver, 1 stream)",
                                            variable=self.use_ffms2_var)
        self.use_ffms2_button.bind("<Button-1>", self.on_set_ffms)
        self.use_ffms2_button.grid(row=4, column=1, sticky='w', pady=0, padx=0)

        self.transcode_video_var = BooleanVar()
        self.transcode_video_var.set(False)
        self.transcode_video_button = Checkbutton(self, text="Raw transcode video first",
                                                  variable=self.transcode_video_var)
        self.transcode_video_button.grid(row=5, column=1, sticky='w', pady=0, padx=0)

        self.transcode_audio_var = BooleanVar()
        self.transcode_audio_var.set(False)
        self.transcode_audio_button = Checkbutton(self, text="Raw transcode audio first",
                                                  variable=self.transcode_audio_var)
        self.transcode_audio_button.grid(row=6, column=1, sticky='w', pady=0, padx=0)

        self.corrupt_var = BooleanVar()
        self.corrupt_var.set(False)
        self.corrupt_var_button = Checkbutton(self, text="Fix AVC (ts) corruption",
                                              variable=self.corrupt_var)
        self.corrupt_var_button.grid(row=7, column=1, sticky='w', pady=0, padx=0)

        self.stabilize_var = BooleanVar()
        self.stabilize_var.set(False)
        self.stabilize_var_button = Checkbutton(self, text="Stabilize (one global instance)",
                                                variable=self.stabilize_var)
        self.stabilize_var_button.grid(row=8, column=1, sticky='w', pady=0, padx=0)

        self.audio_codec_label = Label(self)
        self.audio_codec_label["text"] = "Audio Codec: "
        self.audio_codec_var = StringVar()
        self.audio_codec_var.set("aac")
        self.audio_codec_label.grid(row=9, column=0, sticky='SE', pady=0, padx=0)

        self.audio_codec_button = Radiobutton(self, text="LAME MP3", variable=self.audio_codec_var,
                                              value="libmp3lame")
        self.audio_codec_button.grid(row=9, column=1, sticky='w', pady=0, padx=0)
        self.audio_codec_button = Radiobutton(self, text="AAC", variable=self.audio_codec_var, value="aac")
        self.audio_codec_button.grid(row=10, column=1, sticky='w', pady=0, padx=0)
        self.audio_codec_button = Radiobutton(self, text="Opus", variable=self.audio_codec_var, value="libopus")
        self.audio_codec_button.grid(row=11, column=1, sticky='w', pady=0, padx=0)
        self.audio_codec_button = Radiobutton(self, text="PCM32 (raw)", variable=self.audio_codec_var,
                                              value="pcm_s32le")
        self.audio_codec_button.grid(row=12, column=1, sticky='w', pady=0, padx=0)

        self.codec_label = Label(self)
        self.codec_label["text"] = "Video Codec: "
        self.codec_var = StringVar()
        self.codec_var.set("libx265")
        self.codec_label.grid(row=12, column=0, sticky='SE', pady=0, padx=0)

        self.video_codec_button = Radiobutton(self, text="x264", variable=self.codec_var, value="libx264")
        self.video_codec_button.grid(row=13, column=1, sticky='w', pady=0, padx=0)
        self.video_codec_button = Radiobutton(self, text="x265", variable=self.codec_var, value="libx265")
        self.video_codec_button.grid(row=14, column=1, sticky='w', pady=0, padx=0)
        self.video_codec_button = Radiobutton(self, text="V9", variable=self.codec_var, value="libvpx-vp9")
        self.video_codec_button.grid(row=15, column=1, sticky='w', pady=0, padx=0)
        self.video_codec_button = Radiobutton(self, text="raw", variable=self.codec_var, value="rawvideo")
        self.video_codec_button.grid(row=16, column=1, sticky='w', pady=0, padx=0)

        self.speed_label = Label(self)
        self.speed_label["text"] = "Encoding Speed: "
        self.speed_label.grid(row=17, column=0, sticky='SE', pady=0, padx=0)

        self.speed = tk.Scale(self, from_=0, to=6, orient=HORIZONTAL, sliderrelief=FLAT)
        self.speed.grid(row=17, column=1, sticky='WE', pady=0, padx=0)
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

        self.progress_label = Label(self)
        self.progress_label["text"] = f"Progress: "
        self.progress_label.grid(row=25, column=0, sticky='NE', padx=0)

        self.status_var = StringVar()
        self.status_var.set("Idle...")
        self.status_bar = Label(self, textvariable=self.status_var, width=70)
        self.status_bar.grid(row=25, column=1, columnspan=5, sticky='W', pady=0, padx=0)

        self.replace_button_var = BooleanVar()
        self.replace_button_var.set(False)
        self.replace_button = Checkbutton(self, text="Replace Original File(s)",
                                          variable=self.replace_button_var)

        self.replace_button.grid(row=26, column=1, sticky='SE', pady=0, padx=0)

        self.run = Button(self, text="Run", style='W.TButton', command=lambda: self.run_cmd())
        self.run.grid(row=0, column=2, sticky='WE', padx=0)

        self.stop = Button(self, text="Stop", style='W.TButton', command=lambda: self.stop_process(True))
        self.stop.grid(row=1, column=2, sticky='WE', padx=0)

        self.quit = Button(self, text="Quit", style='W.TButton', command=self.exit)
        self.quit.grid(row=2, column=2, sticky='WE', padx=0, pady=(0, 5))


def get_logger(filename):
    logFormatter = logging.Formatter("%(asctime)s [%(threadName)-12.12s] [%(levelname)-5.5s]  %(message)s")
    rootLogger = logging.getLogger()
    rootLogger.propagate = False
    rootLogger.setLevel(logging.INFO)
    fileHandler = logging.FileHandler(f"{filename}.log")
    fileHandler.setFormatter(logFormatter)
    rootLogger.addHandler(fileHandler)
    consoleHandler = logging.StreamHandler()
    consoleHandler.setFormatter(logFormatter)
    rootLogger.addHandler(consoleHandler)
    return rootLogger


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

    app = Application(master=root)
    app.mainloop()
