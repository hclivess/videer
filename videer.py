import threading
import time
import tkinter as tk
import tkinter.messagebox as messagebox
import tkinter.filedialog as fd
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

    def queue(self, files, info_box):

        def create_command(file):
            command = []
            command.append(f'ffmpeg.exe -hide_banner')
            if self.use_avisynth_var.get():
                command.append(f'-i "parameters.avs" -y')
                CreateAvs(infile=file)
            else:
                command.append(f'-i "{file}" -y')

            self.filename = f"{file}_{self.crf.get()}{self.codec_var.get()}_{self.audio_codec_var.get()}{self.abr.get()}.mp4"
            command.append(f'-c:v {self.codec_var.get()}')
            command.append(f'-preset {self.preset_get(self.speed.get())}')
            command.append(f'-map 0')
            command.append(f'-crf {self.crf.get()}')
            command.append(f'-c:a {self.audio_codec_var.get()}')
            command.append(f'-b:a {self.abr.get()}k')
            command.append('-metadata description="Made with Videer https://github.com/hclivess/videer"')
            command.append('-movflags')
            command.append('+faststart')
            command.append('-bf 2')
            command.append('-flags')
            command.append('+cgop')
            command.append('-pix_fmt yuv420p')
            command.append(f'-f mp4 {self.filename}')
            command.append(f'{self.extras_value.get()}')
            return " ".join(command)

        for file in enumerate(files):
            """automatically ends on Popen termination"""
            info_box.configure(state='normal')
            info_box.insert(tk.INSERT, f"Processing {file[0] + 1}/{len(files)}: {file[1].split('/')[-1]}\n")
            info_box.configure(state='disabled')

            command_line = create_command(file[1])

            rootLogger.info(f"Working on {file[1]}")
            process = subprocess.Popen(command_line, shell=True)
            self.pid = process.pid
            process.wait()

            if info_box:
                info_box.configure(state='normal')
                info_box.insert(tk.INSERT, f"Finished {file[1]}\n")
                info_box.configure(state='disabled')
            self.pid = None

            if self.replace_button_var.get():
                self.replace_file(rename_from=self.filename,
                                  rename_to=file[1])

        info_box.configure(state='normal')
        info_box.insert(tk.INSERT, f"Queue finished")
        info_box.configure(state='disabled')

        playsound("done.mp3")

    def runfx(self):

        self.top = tk.Toplevel()
        self.top.title("Queue Info")
        info_box = tk.Text(self.top, width=100)
        info_box.grid(row=0, pady=0)
        info_box.configure(state='disabled')

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
        os.replace(rename_from, rename_to)

    def create_widgets(self):

        self.deinterlace_var = tk.BooleanVar()
        self.deinterlace_var.set(False)
        self.deinterlace_button = tk.Checkbutton(self, text="Deinterlace", variable=self.deinterlace_var)
        self.deinterlace_button.bind("<Button-1>", self.set_avisynth_true)
        self.deinterlace_button.grid(row=0, column=1, sticky='w', pady=5, padx=5)

        self.use_avisynth_var = tk.BooleanVar()
        self.use_avisynth_var.set(False)
        self.use_avisynth_button = tk.Checkbutton(self, text="Use AviSynth", variable=self.use_avisynth_var)
        self.use_avisynth_button.bind("<Button-1>", self.set_deinterlace_false)
        self.use_avisynth_button.grid(row=1, column=1, sticky='w', pady=5, padx=5)

        self.use_ffms2_var = tk.BooleanVar()
        self.use_ffms2_var.set(False)
        self.use_ffms2_button = tk.Checkbutton(self, text="Use ffms2 (not frameserver compatible)",
                                               variable=self.use_ffms2_var)
        self.use_ffms2_button.bind("<Button-1>", self.set_avisynth_true)
        self.use_ffms2_button.grid(row=2, column=1, sticky='w', pady=5, padx=5)

        self.audio_codec_label = tk.Label(self)
        self.audio_codec_label["text"] = "Audio Codec: "
        self.audio_codec_var = tk.StringVar()
        self.audio_codec_var.set("aac")
        self.audio_codec_label.grid(row=3, column=0, sticky='', pady=5, padx=5)

        self.audio_codec_button = tk.Radiobutton(self, text="LAME MP3", variable=self.audio_codec_var,
                                                 value="libmp3lame")
        self.audio_codec_button.grid(row=3, column=1, sticky='w', pady=5, padx=5)
        self.audio_codec_button = tk.Radiobutton(self, text="AAC", variable=self.audio_codec_var, value="aac")
        self.audio_codec_button.grid(row=4, column=1, sticky='w', pady=5, padx=5)
        self.audio_codec_button = tk.Radiobutton(self, text="Opus", variable=self.audio_codec_var, value="libopus")
        self.audio_codec_button.grid(row=5, column=1, sticky='w', pady=5, padx=5)

        self.codec_label = tk.Label(self)
        self.codec_label["text"] = "Video Codec: "
        self.codec_var = tk.StringVar()
        self.codec_var.set("libx265")
        self.codec_label.grid(row=6, column=0, sticky='', pady=5, padx=5)

        self.codec_button = tk.Radiobutton(self, text="x264", variable=self.codec_var, value="libx264")
        self.codec_button.grid(row=6, column=1, sticky='w', pady=5, padx=5)
        self.codec_button = tk.Radiobutton(self, text="x265", variable=self.codec_var, value="libx265")
        self.codec_button.grid(row=7, column=1, sticky='w', pady=5, padx=5)
        self.codec_button = tk.Radiobutton(self, text="V9", variable=self.codec_var, value="libvpx-vp9")
        self.codec_button.grid(row=8, column=1, sticky='w', pady=5, padx=5)

        self.speed = tk.Scale(self, from_=0, to=6, orient=tk.HORIZONTAL, label="Encoding Speed")
        self.speed.grid(row=9, column=1, sticky='WE', pady=5, padx=5)
        self.speed.set(3)

        self.infile_value = tk.StringVar()
        self.infile_value.set("c:/test.avi")

        self.infile_label = tk.Label(self)
        self.infile_label["text"] = "Input File(s): "
        self.infile_label.grid(row=15, column=0, sticky='', pady=5, padx=5)
        self.infile_button = tk.Button(self, text="Select", fg="green",
                                       command=lambda: self.select_file(self.infile_value))
        self.infile_button.grid(row=15, column=2, sticky='WE', padx=5, pady=(5))

        self.infile = tk.Entry(self, textvariable=self.infile_value, width=70)
        self.infile.grid(row=15, column=1, sticky='W', padx=5)

        self.crf = tk.Scale(self, from_=0, to=51, orient=tk.HORIZONTAL, label="Video CRF")
        self.crf.grid(row=16, column=1, sticky='WE', pady=5, padx=5)
        self.crf.set(24)

        self.abr = tk.Scale(self, from_=0, to=384, orient=tk.HORIZONTAL, label="Audio ABR", resolution=16)
        self.abr.grid(row=17, column=1, sticky='WE', pady=5, padx=5)
        self.abr.set(256)

        self.extras_label = tk.Label(self)
        self.extras_label["text"] = "FFmpeg Extras: "
        self.extras_label.grid(row=18, column=0, sticky='', padx=5)
        self.extras_value = tk.StringVar()
        self.extras_value.set("")
        self.extras = tk.Entry(self, textvariable=self.extras_value, width=70)
        self.extras.grid(row=18, column=1, sticky='W', pady=5, padx=5)

        self.avisynth_extras_label = tk.Label(self)
        self.avisynth_extras_label["text"] = "AviSynth Extras: "
        self.avisynth_extras_label.grid(row=19, column=0, sticky='', padx=5)
        self.avisynth_extras = tk.Text(self, height=2, width=30)
        self.avisynth_extras.grid(row=19, column=1, sticky='WE', pady=5, padx=5)
        # self.avisynth_extras.insert(tk.END, "Just a text Widget\nin two lines\n")

        self.replace_button_var = tk.StringVar()
        self.replace_button_var.set(0)
        self.replace_button = tk.Checkbutton(self, text="Replace Original File(s)",
                                             variable=self.replace_button_var)

        self.replace_button.grid(row=20, column=1, sticky='w', pady=5, padx=5)

        self.run = tk.Button(self, text="Run", fg="green", command=lambda: self.runfx())
        self.run.grid(row=21, column=1, sticky='WE', padx=5)

        self.stop = tk.Button(self, text="Stop", fg="red", command=lambda: self.stop_process(True))
        self.stop.grid(row=22, column=1, sticky='WE', padx=5)

        self.quit = tk.Button(self, text="Quit", fg="red", command=self.exit)
        self.quit.grid(row=23, column=1, sticky='WE', padx=5, pady=(0, 5))


if __name__ == "__main__":
    root = tk.Tk()
    root.wm_title("videer")
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
