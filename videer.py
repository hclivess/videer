import threading
import time
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


class CreateAvs():
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
                avsfile.write('\n')
                avsfile.write(f'A = FFAudioSource("{infile}")')
                avsfile.write('\n')
                avsfile.write(f'V = FFVideoSource("{infile}")')
                avsfile.write('\n')
                avsfile.write('AudioDub(V, A)')
            else:
                avsfile.write(f'AVISource("{infile}", audio=true)')
            avsfile.write('\n')

            avsfile.write('SetFilterMTMode("FFVideoSource", 3)')
            avsfile.write('\n')
            avsfile.write('ConvertToYV24(matrix="rec709")')
            avsfile.write('\n')
            avsfile.write(f'EdiThreads={multiprocessing.cpu_count()}')
            avsfile.write('\n')
            avsfile.write(f'Prefetch({multiprocessing.cpu_count()})')
            avsfile.write('\n')

            if app.avisynth_extras.get("1.0", tk.END).strip():
                avsfile.write(app.avisynth_extras.get("1.0", tk.END))
                avsfile.write('\n')

            if app.deinterlace_var.get():
                avsfile.write(f'QTGMC(Preset="{app.preset_get(app.speed.get())}")')
                avsfile.write('\n')


class Application(tk.Frame):
    def __init__(self, master=None):

        super().__init__(master)
        self.master = master

        self.grid()
        self.create_widgets()
        self.file_queue = []
        self.pid = None
        self.filename = None
        self.tempfile = None
        self.original_fn = None

    def assemble(self, file):
        command = []
        command.append(f'ffmpeg.exe -hide_banner')
        if self.use_avisynth_var.get():
            command.append(f'-i "parameters.avs" -y')
            CreateAvs(infile=file)
        else:
            command.append(f'-i "{file}" -y')

        self.filename = f'{file}_{self.crf.get()}{self.codec_var.get()}_{self.audio_codec_var.get()}{self.abr.get()}.mkv'
        command.append(f'-c:v {self.codec_var.get()}')
        command.append(f'-preset {self.preset_get(self.speed.get())}')
        command.append(f'-map 0:v -map 0:a -map 0:s?')
        command.append(f'-crf {self.crf.get()}')
        command.append(f'-c:a {self.audio_codec_var.get()}')
        command.append(f'-b:a {self.abr.get()}k')
        command.append(f'-c:s copy')
        command.append('-metadata comment="Made with Videer https://github.com/hclivess/videer"')
        command.append(f'-metadata description="'
                       f'Video Codec: {self.codec_var.get()}, '
                       f'Preset: {self.preset_get(self.speed.get())}, '
                       f'CRF: {self.crf.get()}, '
                       f'Audio Codec: {self.audio_codec_var.get()}, '
                       f'Audio Bitrate: {self.abr.get()}k"')
        command.append('-movflags')
        command.append('+faststart')
        command.append('-bf 2')
        command.append('-flags')
        command.append('+cgop')
        command.append('-pix_fmt yuv420p')
        command.append(f'-f matroska "{self.filename}"')
        command.append(f'{self.extras_value.get()}')
        return " ".join(command)

    def transcode(self, file, transcode_video, transcode_audio, corrupt_x264_hack):
        no_ext = os.path.splitext(file)[0]
        temp_transcode = None

        if transcode_video and transcode_audio:
            temp_transcode = f'ffmpeg.exe -hide_banner -i "{file}" -preset medium {corrupt_x264_hack} -map 0:v -map 0:a -map 0:s? -c:a pcm_s32le -c:v rawvideo -c:s copy -hide_banner "{no_ext}_temp.avi" -y'
        elif transcode_video and not transcode_audio:
            temp_transcode = f'ffmpeg.exe -hide_banner -i "{file}" -preset medium {corrupt_x264_hack} -map 0:v -map 0:a -map 0:s? -c:a copy -c:v rawvideo -c:s copy -hide_banner "{no_ext}_temp.avi" -y'
        elif transcode_audio and not transcode_video:
            temp_transcode = f'ffmpeg.exe -hide_banner -i "{file}" -preset medium {corrupt_x264_hack} -map 0:v -map 0:a -map 0:s? -c:a pcm_s32le -c:v copy -c:s copy -hide_banner "{no_ext}_temp.avi" -y'

        self.open_process(temp_transcode)
        self.tempfile = f"{no_ext}_temp.avi"
        return self.tempfile

    def open_process(self, command_line):
        process = subprocess.Popen(command_line)
        # process = subprocess.Popen(command_line, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        # stdout, stderr = process.communicate()
        process.communicate()
        return_code = process.returncode
        self.pid = process.pid
        rootLogger.info(f"Return code: {return_code}")
        process.wait()
        return return_code

    def queue(self, files, info_box):
        """automatically ends on Popen termination"""

        for file in enumerate(files):

            input_name = file[1]
            self.original_fn = input_name

            info_box.configure(state='normal')
            info_box.insert(tk.END, f"Processing {file[0] + 1}/{len(files)}: "
                                    f"{input_name.split('/')[-1]}\n"
                            )
            info_box.configure(state='disabled')
            rootLogger.info(f"Processing {input_name}")

            should_transcode_video = False
            should_transcode_audio = False
            if int(self.transcode_video_var.get()) == 1:
                should_transcode_video = True
            if int(self.transcode_audio_var.get()) == 1:
                should_transcode_audio = True
            if int(self.corrupt_var.get()) == 1:
                corrupt_hack = "-bsf:v h264_mp4toannexb"
            else:
                corrupt_hack = ""

            print(should_transcode_video, should_transcode_audio)

            if should_transcode_video or should_transcode_audio:
                input_name = self.transcode(input_name,
                                            transcode_video=should_transcode_video,
                                            transcode_audio=should_transcode_audio,
                                            corrupt_x264_hack=corrupt_hack)

            command_line = self.assemble(input_name)
            return_code = self.open_process(command_line)

            if info_box:
                info_box.configure(state='normal')
                if return_code == 0:
                    """error code can be None, force numeric check"""
                    info_box.insert(tk.END, f"Finished {input_name.split('/')[-1]}: "
                                            f"{int((file[0] + 1) / (len(files)) * 100)}% \n")
                    rootLogger.info(f"Finished {input_name}")
                else:
                    info_box.insert(tk.END, f"Error with {input_name.split('/')[-1]}: "
                                            f"{int((file[0] + 1) / (len(files)) * 100)}% \n")
                    rootLogger.info(f"Error with {input_name}")

                    if os.path.exists(self.filename):
                        os.rename(self.filename, f"{self.filename}.error")

                info_box.configure(state='disabled')

            if int(self.replace_button_var.get()) == 1 and return_code == 0:
                self.replace_file(rename_from=self.filename,
                                  rename_to=self.original_fn)

            if self.tempfile:
                os.remove(self.tempfile)

            ffindex = f"{input_name}.ffindex"
            if os.path.exists(ffindex):
                os.remove(ffindex)

            self.pid = None

        info_box.configure(state='normal')
        info_box.insert(tk.END, "Queue finished")
        info_box.configure(state='disabled')
        rootLogger.info("Queue finished")

        playsound("done.mp3")

    def create_info_box(self):
        self.top = tk.Toplevel()
        self.top.title("Queue Info")
        self.top.resizable(False, False)

        info_box = st.ScrolledText(self.top, width=100)
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

    def set_avisynth_true(self, click):
        if not self.deinterlace_var.get():
            self.use_avisynth_var.set(True)

    def set_deinterlace_false(self, click):
        if self.use_avisynth_var.get():
            self.deinterlace_var.set(False)

    def exit(self):
        self.stop_process()
        self.master.destroy()

    def stop_process(self, announce=False):
        if self.pid:
            output = f"Process {self.pid} Terminated"
            process = psutil.Process(self.pid)
            for proc in process.children(recursive=True):
                proc.kill()
            process.kill()
            self.pid = None
        else:
            output = f"No relevant process found"

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
            os.replace(rename_from, new_name_ext)
            if rename_to != new_name_ext:
                os.remove(rename_to)

    def create_widgets(self):

        self.deinterlace_var = tk.BooleanVar()
        self.deinterlace_var.set(False)
        self.deinterlace_button = tk.Checkbutton(self, text="Deinterlace", variable=self.deinterlace_var)
        self.deinterlace_button.bind("<Button-1>", self.set_avisynth_true)
        self.deinterlace_button.grid(row=0, column=1, sticky='w', pady=5, padx=5)

        self.use_avisynth_var = tk.BooleanVar()
        self.use_avisynth_var.set(False)
        self.use_avisynth_button = tk.Checkbutton(self, text="Use AviSynth+", variable=self.use_avisynth_var)
        self.use_avisynth_button.bind("<Button-1>", self.set_deinterlace_false)
        self.use_avisynth_button.grid(row=1, column=1, sticky='w', pady=5, padx=5)

        self.use_ffms2_var = tk.BooleanVar()
        self.use_ffms2_var.set(False)
        self.use_ffms2_button = tk.Checkbutton(self, text="Use ffms2 (no frameserver, 1 stream)",
                                               variable=self.use_ffms2_var)
        self.use_ffms2_button.bind("<Button-1>", self.set_avisynth_true)
        self.use_ffms2_button.grid(row=2, column=1, sticky='w', pady=5, padx=5)

        self.transcode_video_var = tk.BooleanVar()
        self.transcode_video_var.set(False)
        self.transcode_video_button = tk.Checkbutton(self, text="Raw transcode video first",
                                                     variable=self.transcode_video_var)
        self.transcode_video_button.grid(row=3, column=1, sticky='w', pady=5, padx=5)

        self.transcode_audio_var = tk.BooleanVar()
        self.transcode_audio_var.set(False)
        self.transcode_audio_button = tk.Checkbutton(self, text="Raw transcode audio first",
                                                     variable=self.transcode_audio_var)
        self.transcode_audio_button.grid(row=4, column=1, sticky='w', pady=5, padx=5)

        self.corrupt_var = tk.BooleanVar()
        self.corrupt_var.set(False)
        self.corrupt_var_button = tk.Checkbutton(self, text="Fix AVC (ts) corruption during raw transcode",
                                                     variable=self.corrupt_var)
        self.corrupt_var_button.grid(row=5, column=1, sticky='w', pady=5, padx=5)

        self.audio_codec_label = tk.Label(self)
        self.audio_codec_label["text"] = "Audio Codec: "
        self.audio_codec_var = tk.StringVar()
        self.audio_codec_var.set("aac")
        self.audio_codec_label.grid(row=6, column=0, sticky='', pady=5, padx=5)

        self.audio_codec_button = tk.Radiobutton(self, text="LAME MP3", variable=self.audio_codec_var,
                                                 value="libmp3lame")
        self.audio_codec_button.grid(row=6, column=1, sticky='w', pady=5, padx=5)
        self.audio_codec_button = tk.Radiobutton(self, text="AAC", variable=self.audio_codec_var, value="aac")
        self.audio_codec_button.grid(row=7, column=1, sticky='w', pady=5, padx=5)
        self.audio_codec_button = tk.Radiobutton(self, text="Opus", variable=self.audio_codec_var, value="libopus")
        self.audio_codec_button.grid(row=8, column=1, sticky='w', pady=5, padx=5)
        self.audio_codec_button = tk.Radiobutton(self, text="PCM32 (raw)", variable=self.audio_codec_var,
                                                 value="pcm_s32le")
        self.audio_codec_button.grid(row=9, column=1, sticky='w', pady=5, padx=5)

        self.codec_label = tk.Label(self)
        self.codec_label["text"] = "Video Codec: "
        self.codec_var = tk.StringVar()
        self.codec_var.set("libx265")
        self.codec_label.grid(row=10, column=0, sticky='', pady=5, padx=5)

        self.video_codec_button = tk.Radiobutton(self, text="x264", variable=self.codec_var, value="libx264")
        self.video_codec_button.grid(row=10, column=1, sticky='w', pady=5, padx=5)
        self.video_codec_button = tk.Radiobutton(self, text="x265", variable=self.codec_var, value="libx265")
        self.video_codec_button.grid(row=11, column=1, sticky='w', pady=5, padx=5)
        self.video_codec_button = tk.Radiobutton(self, text="V9", variable=self.codec_var, value="libvpx-vp9")
        self.video_codec_button.grid(row=12, column=1, sticky='w', pady=5, padx=5)
        self.video_codec_button = tk.Radiobutton(self, text="raw", variable=self.codec_var, value="rawvideo")
        self.video_codec_button.grid(row=13, column=1, sticky='w', pady=5, padx=5)

        self.speed = tk.Scale(self, from_=0, to=6, orient=tk.HORIZONTAL, label="Encoding Speed")
        self.speed.grid(row=15, column=1, sticky='WE', pady=5, padx=5)
        self.speed.set(3)

        self.infile_value = tk.StringVar()
        self.infile_value.set("c:/test.avi")

        self.infile_label = tk.Label(self)
        self.infile_label["text"] = "Input File(s): "
        self.infile_label.grid(row=20, column=0, sticky='', pady=5, padx=5)
        self.infile_button = tk.Button(self, text="Select", fg="green",
                                       command=lambda: self.select_file(self.infile_value))
        self.infile_button.grid(row=20, column=2, sticky='WE', padx=5, pady=(5))

        self.infile = tk.Entry(self, textvariable=self.infile_value, width=70)
        self.infile.grid(row=20, column=1, sticky='W', padx=5)

        self.crf = tk.Scale(self, from_=0, to=51, orient=tk.HORIZONTAL, label="Video CRF")
        self.crf.grid(row=21, column=1, sticky='WE', pady=5, padx=5)
        self.crf.set(23)

        self.abr = tk.Scale(self, from_=0, to=384, orient=tk.HORIZONTAL, label="Audio ABR", resolution=16)
        self.abr.grid(row=22, column=1, sticky='WE', pady=5, padx=5)
        self.abr.set(256)

        self.extras_label = tk.Label(self)
        self.extras_label["text"] = "FFmpeg Extras: "
        self.extras_label.grid(row=23, column=0, sticky='', padx=5)
        self.extras_value = tk.StringVar()
        self.extras_value.set("")
        self.extras = tk.Entry(self, textvariable=self.extras_value, width=70)
        self.extras.grid(row=23, column=1, sticky='W', pady=5, padx=5)

        self.avisynth_extras_label = tk.Label(self)
        self.avisynth_extras_label["text"] = "AviSynth Extras: "
        self.avisynth_extras_label.grid(row=24, column=0, sticky='', padx=5)
        self.avisynth_extras = tk.Text(self, height=2, width=30)
        self.avisynth_extras.grid(row=24, column=1, sticky='WE', pady=5, padx=5)

        self.replace_button_var = tk.StringVar()
        self.replace_button_var.set(0)
        self.replace_button = tk.Checkbutton(self, text="Replace Original File(s)",
                                             variable=self.replace_button_var)

        self.replace_button.grid(row=25, column=1, sticky='w', pady=5, padx=5)

        self.run = tk.Button(self, text="Run", fg="green", command=lambda: self.run_cmd())
        self.run.grid(row=26, column=1, sticky='WE', padx=5)

        self.stop = tk.Button(self, text="Stop", fg="red", command=lambda: self.stop_process(True))
        self.stop.grid(row=27, column=1, sticky='WE', padx=5)

        self.quit = tk.Button(self, text="Quit", fg="red", command=self.exit)
        self.quit.grid(row=28, column=1, sticky='WE', padx=5, pady=(0, 5))


if __name__ == "__main__":
    root = tk.Tk()
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
