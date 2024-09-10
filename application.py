import os
import subprocess
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
from file_handler import File
from avisynth import CreateAvs
from utils import multiple_replace, assemble_final, get_logger

class Application(ttk.Frame):
    def __init__(self, master=None):
        super().__init__(master)
        self.master = master
        self.top = None

        self.grid()
        self.create_widgets()
        self.file_queue = []
        self.tempfile = None
        self.should_transcode = False
        self.process = None
        self.workdir = None
        self.should_stop = False
        self.path = os.getcwd()

    def transcode(self, fileobj, transcode_video, transcode_audio):
        fileobj.log.info("Transcode process started")
        temp_transcode = None

        preset = self.preset_get(int(self.speed.get())).replace(" ", "")
        base_command = f'ffmpeg.exe -err_detect crccheck+bitstream+buffer -hide_banner -i "{fileobj.filename}" -preset {preset} -map 0:v -map 0:a? -map 0:s?'

        if transcode_video and transcode_audio:
            temp_transcode = f'{base_command} -c:a pcm_s32le -c:v rawvideo -c:s copy "{fileobj.transcodename}" -y'
        elif transcode_video and not transcode_audio:
            temp_transcode = f'{base_command} -c:a copy -c:v rawvideo -c:s copy "{fileobj.transcodename}" -y'
        elif transcode_audio and not transcode_video:
            temp_transcode = f'{base_command} -c:a pcm_s32le -c:v copy -c:s copy "{fileobj.transcodename}" -y'

        self.open_process(temp_transcode, fileobj)

    def open_process(self, command_line, fileobj):
        fileobj.log.info(f"Executing command {command_line}")

        with subprocess.Popen(command_line,
                              stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT,
                              universal_newlines=True,
                              encoding="utf8") as self.process:

            errors = ["error", "invalid"]
            for line in self.process.stdout:
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
                if any(error in line.lower() for error in errors):
                    fileobj.ffmpeg_errors.append(line.strip())

        return_code = self.process.wait()

        fileobj.log.info(f"Return code: {return_code}")
        return return_code

    def queue(self, files, info_box):
        for i, f in enumerate(files):
            if self.should_stop:
                self.should_stop = False
                return

            fileobj = File(number=i, filename=f)

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

                should_transcode_video = self.transcode_video_var.get()
                should_transcode_audio = self.transcode_audio_var.get()
                self.should_transcode = should_transcode_video or should_transcode_audio

                if self.should_transcode and not self.should_stop:
                    self.transcode(fileobj,
                                   transcode_video=should_transcode_video,
                                   transcode_audio=should_transcode_audio)

                if self.should_transcode:
                    fileobj.filename = fileobj.transcodename

                if self.use_avisynth_var.get() and not self.should_stop:
                    CreateAvs(fileobj, self)

                final_cmd = assemble_final(fileobj, self)
                return_code = self.open_process(final_cmd, fileobj)

            else:
                return_code = 1

            if return_code == 0 and not self.should_stop:
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
                                  rename_to=fileobj.orig_name,
                                  log=fileobj.log)

            for file in [fileobj.transcodename, fileobj.ffindex, fileobj.tempffindex, fileobj.avsfile]:
                if os.path.exists(file) and not self.should_stop:
                    os.remove(file)

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
        self.top = tk.Toplevel()
        self.top.title("Queue Info")
        self.top.resizable(False, False)

        info_box = scrolledtext.ScrolledText(self.top, width=100, font=('calibri', 10, 'bold'))
        info_box.grid(row=0, pady=0)
        info_box.configure(state='disabled')
        return info_box

    def run_cmd(self):
        info_box = self.create_info_box()
        file_thread = threading.Thread(target=self.queue, args=(self.file_queue, info_box,))
        file_thread.start()

    def select_file(self, var):
        files = filedialog.askopenfilename(multiple=True, initialdir="", title="Select file")
        self.file_queue = list(files)
        var.set(self.file_queue)

    def on_set_avisynth(self, click):
        if self.use_avisynth_var.get():
            self.tff_var.set(False)
            self.use_ffms2_var.set(False)
            self.deinterlace_var.set(False)

    def on_set_deinterlace(self, click):
        if not self.deinterlace_var.get() and not self.use_avisynth_var.get():
            self.use_avisynth_var.set(True)
        elif self.deinterlace_var.get() and self.use_avisynth_var.get() and not self.use_ffms2_var.get():
            self.reduce_fps_var.set(False)
            self.tff_var.set(False)
        elif self.deinterlace_var.get() and self.use_avisynth_var.get():
            self.tff_var.set(False)

    def on_set_ttf(self, click):
        if not self.tff_var.get() and not self.deinterlace_var.get():
            self.deinterlace_var.set(True)
            self.use_avisynth_var.set(True)

    def on_set_reduce(self, click):
        if not self.reduce_fps_var.get() and not self.deinterlace_var.get():
            self.deinterlace_var.set(True)
            self.use_avisynth_var.set(True)

    def on_set_ffms(self, click):
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
            output = "Process terminated"
            self.process.kill()
        else:
            output = "No process assigned"

        if announce:
            messagebox.showinfo(title="Info", message=output)

        if self.top:
            self.top.destroy()

    def preset_get(self, number: int):
        config_dict = {0: "very slow", 1: "slower", 2: "slow", 3: "medium", 4: "faster", 5: "fast", 6: "ultrafast"}
        return config_dict.get(number)

    def replace_file(self, rename_from, rename_to, log):
        log.info(f"Replacing {rename_to} with {rename_from} file as requested")
        if os.path.exists(rename_from):
            os.rename(rename_to, f"{rename_to}.old")
        os.rename(rename_from, rename_to)

    def create_widgets(self):
        self.deinterlace_var = tk.BooleanVar()
        self.deinterlace_var.set(False)
        self.deinterlace_button = ttk.Checkbutton(self, text="Deinterlace", variable=self.deinterlace_var)
        self.deinterlace_button.bind("<Button-1>", self.on_set_deinterlace)
        self.deinterlace_button.grid(row=0, column=1, sticky='w', pady=0, padx=0)

        self.tff_var = tk.BooleanVar()
        self.tff_var.set(False)
        self.tff_button = ttk.Checkbutton(self, text="Top Field First", variable=self.tff_var)
        self.tff_button.bind("<Button-1>", self.on_set_ttf)
        self.tff_button.grid(row=1, column=1, sticky='w', pady=0, padx=0)

        self.reduce_fps_var = tk.BooleanVar()
        self.reduce_fps_var.set(False)
        self.reduce_fps_button = ttk.Checkbutton(self, text="Reduce Frames", variable=self.reduce_fps_var)
        self.reduce_fps_button.bind("<Button-1>", self.on_set_reduce)
        self.reduce_fps_button.grid(row=2, column=1, sticky='w', pady=0, padx=0)

        self.use_avisynth_var = tk.BooleanVar()
        self.use_avisynth_var.set(False)
        self.use_avisynth_button = ttk.Checkbutton(self, text="Use AviSynth+", variable=self.use_avisynth_var)
        self.use_avisynth_button.bind("<Button-1>", self.on_set_avisynth)
        self.use_avisynth_button.grid(row=3, column=1, sticky='w', pady=0, padx=0)

        self.use_ffms2_var = tk.BooleanVar()
        self.use_ffms2_var.set(True)
        self.use_ffms2_button = ttk.Checkbutton(self, text="Use ffms2 (No Frameserver, 1 Stream)",
                                            variable=self.use_ffms2_var)
        self.use_ffms2_button.bind("<Button-1>", self.on_set_ffms)
        self.use_ffms2_button.grid(row=4, column=1, sticky='w', pady=0, padx=0)

        self.transcode_video_var = tk.BooleanVar()
        self.transcode_video_var.set(False)
        self.transcode_video_button = ttk.Checkbutton(self, text="Raw Transcode Video First",
                                                  variable=self.transcode_video_var)
        self.transcode_video_button.grid(row=5, column=1, sticky='w', pady=0, padx=0)

        self.transcode_audio_var = tk.BooleanVar()
        self.transcode_audio_var.set(False)
        self.transcode_audio_button = ttk.Checkbutton(self, text="Raw Transcode Audio First",
                                                  variable=self.transcode_audio_var)
        self.transcode_audio_button.grid(row=6, column=1, sticky='w', pady=0, padx=0)

        self.corrupt_var = tk.BooleanVar()
        self.corrupt_var.set(False)
        self.corrupt_var_button = ttk.Checkbutton(self, text="Fix AVC (ts) Corruption",
                                              variable=self.corrupt_var)
        self.corrupt_var_button.grid(row=7, column=1, sticky='w', pady=0, padx=0)

        self.stereo_var = tk.BooleanVar()
        self.stereo_var.set(False)
        self.stereo_button = ttk.Checkbutton(self, text="Reduce to Stereo (be careful)",
                                             variable=self.stereo_var)
        self.stereo_button.grid(row=8, column=1, sticky='w', pady=0, padx=0)

        self.audio_codec_label = ttk.Label(self, text="Audio Codec: ")
        self.audio_codec_label.grid(row=9, column=0, sticky='SE', pady=0, padx=0)
        self.audio_codec_var = tk.StringVar()
        self.audio_codec_var.set("aac")

        audio_codecs = [("LAME MP3", "libmp3lame"), ("AAC", "aac"), ("Opus", "libopus"),
                        ("PCM32 (Raw)", "pcm_s32le"), ("Copy", "copy")]
        for i, (text, value) in enumerate(audio_codecs):
            ttk.Radiobutton(self, text=text, variable=self.audio_codec_var, value=value).grid(row=9 + i, column=1,
                                                                                              sticky='w', pady=0,
                                                                                              padx=0)

        self.codec_label = ttk.Label(self, text="Video Codec: ")
        self.codec_label.grid(row=15, column=0, sticky='SE', pady=0, padx=0)
        self.codec_var = tk.StringVar()
        self.codec_var.set("libx265")

        video_codecs = [("x264", "libx264"), ("x265", "libx265"), ("CUDA h264 (CQ)", "h264_nvenc"),
                        ("CUDA HEVC (CQ)", "hevc_nvenc"), ("ProRes", "prores_ks"), ("Raw", "rawvideo"),
                        ("Copy", "copy")]
        for i, (text, value) in enumerate(video_codecs):
            ttk.Radiobutton(self, text=text, variable=self.codec_var, value=value).grid(row=15 + i, column=1,
                                                                                        sticky='w',
                                                                                        pady=0, padx=0)

        self.speed_label = ttk.Label(self, text="Encoding Speed: ")
        self.speed_label.grid(row=22, column=0, sticky='SE', pady=0, padx=0)

        self.speed = tk.Scale(self, from_=0, to=6, orient=tk.HORIZONTAL, sliderrelief=tk.FLAT)
        self.speed.grid(row=22, column=1, sticky='WE', pady=0, padx=0)
        self.speed.set(3)

        self.infile_value = tk.StringVar()
        self.infile_value.set("c:/test.avi")

        self.infile_button = ttk.Button(self, text="Input File(s):",
                                        command=lambda: self.select_file(self.infile_value))
        self.infile_button.grid(row=24, column=0, sticky='SE', padx=0, pady=(5))

        self.infile = ttk.Entry(self, textvariable=self.infile_value, width=70)
        self.infile.grid(row=24, column=1, sticky='W', padx=0)

        self.crf_label = ttk.Label(self, text="CRF: ")
        self.crf_label.grid(row=25, column=0, sticky='SE', pady=0, padx=0)
        self.crf = tk.Scale(self, from_=0, to=51, orient=tk.HORIZONTAL, sliderrelief=tk.FLAT)
        self.crf.grid(row=25, column=1, sticky='WE', pady=0, padx=0)
        self.crf.set(23)

        self.abr_label = ttk.Label(self, text="Audio ABR: ")
        self.abr_label.grid(row=26, column=0, sticky='SE', pady=0, padx=0)

        self.abr = tk.Scale(self, resolution=16, from_=0, to=512, orient=tk.HORIZONTAL, sliderrelief=tk.FLAT)
        self.abr.grid(row=26, column=1, sticky='WE', pady=0, padx=0)
        self.abr.set(256)

        self.extras_label = ttk.Label(self, text="FFmpeg Extras: ")
        self.extras_label.grid(row=27, column=0, sticky='SE', padx=0)
        self.extras_value = tk.StringVar()
        self.extras_value.set("")
        self.extras = ttk.Entry(self, textvariable=self.extras_value, width=70)
        self.extras.grid(row=27, column=1, sticky='W', pady=0, padx=0)

        self.avisynth_extras_label = ttk.Label(self, text="AviSynth+ Extras: ")
        self.avisynth_extras_label.grid(row=28, column=0, sticky='SE', padx=0)
        self.avisynth_extras = tk.Text(self, height=2, width=30)
        self.avisynth_extras.grid(row=28, column=1, sticky='WE', pady=0, padx=0)

        self.progress_label = ttk.Label(self, text="Progress: ")
        self.progress_label.grid(row=29, column=0, sticky='NE', padx=0)

        self.status_var = tk.StringVar()
        self.status_var.set("Idle...")
        self.status_bar = ttk.Label(self, textvariable=self.status_var, width=70)
        self.status_bar.grid(row=29, column=1, columnspan=5, sticky='W', pady=0, padx=0)

        self.replace_button_var = tk.BooleanVar()
        self.replace_button_var.set(False)
        self.replace_button = ttk.Checkbutton(self, text="Replace Original File(s)",
                                              variable=self.replace_button_var)
        self.replace_button.grid(row=30, column=1, sticky='SE', pady=0, padx=0)

        self.run = ttk.Button(self, text="Run", style='W.TButton', command=self.run_cmd)
        self.run.grid(row=0, column=2, sticky='WE', padx=0)

        self.stop = ttk.Button(self, text="Stop", style='W.TButton', command=lambda: self.stop_process(True))
        self.stop.grid(row=1, column=2, sticky='WE', padx=0)

        self.quit = ttk.Button(self, text="Quit", style='W.TButton', command=self.exit)
        self.quit.grid(row=2, column=2, sticky='WE', padx=0, pady=(0, 5))

def info_box_insert(info_box, message, log_message=None, logger=None):
    try:
        info_box.configure(state='normal')
        info_box.insert(tk.END, f"{message}\n")
        info_box.configure(state='disabled')
    except Exception as e:
        print(f"Info window closed: {e}")

    if logger:
        logger.info(log_message)