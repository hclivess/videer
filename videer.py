import threading
import tkinter as tk
import tkinter.messagebox as messagebox
import tkinter.filedialog as fd
import os
import logging.handlers
import subprocess


class CreateAvs():
    def __init__(self, infile):
        with open("parameters.avs", "w") as avsfile:
            avsfile.write(f'AVISource("{infile}", audio=true)')
            avsfile.write('\n')
            avsfile.write('ConvertToYV24(matrix="rec709")')

            if app.avisynth_extras.get("1.0", tk.END).strip():
                avsfile.write('\n')
                avsfile.write(app.avisynth_extras.get("1.0", tk.END))

            if app.deinterlace_var.get():
                avsfile.write('\n')
                avsfile.write('QTGMC(Preset="Slower")')

class Application(tk.Frame):
    def __init__(self, master=None):

        super().__init__(master)
        self.master = master

        self.grid()
        self.create_widgets()
        self.file_queue = []

    def do_files(self, files, info_box):

        def create_command(file):
            command = []
            command.append(f'ffmpeg.exe -hide_banner')
            if self.use_avisynth_var.get():
                command.append(f'-i "parameters.avs" -y')
                CreateAvs(infile=file)
            else:
                command.append(f'-i "{file}" -y')
            if self.deshake_var.get():
                command.append('-vf deshake')
            command.append(f'-c:v {self.codec_var.get()}')
            command.append(f'-preset {self.preset_get(self.speed.get())}')
            command.append(f'-crf {self.crf.get()}')
            command.append(f'-c:a {self.audio_codec_var.get()}')
            command.append(f'-b:a {self.abr.get()}k')
            command.append('-movflags')
            command.append('+faststart')
            command.append('-bf 2')
            command.append('-flags')
            command.append('+cgop')
            command.append('-pix_fmt yuv420p')
            command.append(f'-f mp4 "{file}_{self.crf.get()}{self.codec_var.get()}_{self.audio_codec_var.get()}{self.abr.get()}.mp4"')
            command.append(f'{self.extras_value.get()}')
            return " ".join(command)

        for file in files:
            info_box.configure(state='normal')
            info_box.insert(tk.INSERT, f"Processing {file.split('/')[-1]}\n")
            info_box.configure(state='disabled')

            command_line = create_command(file)
            print(f"Executing {command_line}")

            rootLogger.info(f"Working on {file}")
            pipe = subprocess.Popen(command_line, shell=True, stdout=subprocess.PIPE).stdout
            output = pipe.read().decode()
            pipe.close()
            rootLogger.info(output)

            info_box.configure(state='normal')
            info_box.insert(tk.INSERT, f"Finished {file}\n")
            info_box.configure(state='disabled')

        messagebox.showinfo(title="Info", message="Queue finished")


    def runfx(self):

        top = tk.Toplevel()
        top.title("Info")
        info_box = tk.Text(top, width=100)
        info_box.grid(row=0, pady=0)
        info_box.configure(state='disabled')

        file_thread = threading.Thread(target=self.do_files, args=(self.file_queue, info_box, ))
        file_thread.start()

    def select_file(self, var):
        files = fd.askopenfilename(multiple=True, initialdir="", title="Select file")
        del self.file_queue[:]
        for file in files:
            self.file_queue.append(file)
        var.set(self.file_queue)

    def set_avisynth_true(self,click):
        if not self.deinterlace_var.get():
            self.use_avisynth_var.set(True)

    def set_deinterlace_false(self,click):
        if self.use_avisynth_var.get():
            self.deinterlace_var.set(False)

    def exit(self):
        self.stop_process()
        self.master.destroy()

    def stop_process(self, announce=False):
        pipe = subprocess.Popen("Taskkill /IM ffmpeg.exe /F", shell=True, stdout=subprocess.PIPE).stdout
        print("Stop signal sent")
        output = pipe.read().decode()
        if not output:
            output = "No relevant process found"
        if announce:
            messagebox.showinfo(title="Info", message=output)
        pipe.close()

    def preset_get(self, number: int):

        if number == 0:
            preset = "veryslow"
        elif number == 1:
            preset = "slower"
        elif number == 2:
            preset = "slow"
        elif number == 4:
            preset = "faster"
        elif number == 5:
            preset = "fast"
        elif number == 6:
            preset = "fastest"
        else:
            preset = "medium"

        return preset

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

        self.deshake_var = tk.BooleanVar()
        self.deshake_var.set(False)
        self.deshake_button = tk.Checkbutton(self, text="Stabilize", variable=self.deshake_var)
        self.deshake_button.grid(row=2, column=1, sticky='w', pady=5, padx=5)

        self.audio_codec_label = tk.Label(self)
        self.audio_codec_label["text"] = "Audio Codec: "
        self.audio_codec_var = tk.StringVar()
        self.audio_codec_var.set("aac")
        self.audio_codec_label.grid(row=3, column=0, sticky='', pady=5, padx=5)

        self.audio_codec_button = tk.Radiobutton(self, text="LAME MP3", variable=self.audio_codec_var, value="libmp3lame")
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
        self.infile_button = tk.Button(self, text="Select", fg="green", command=lambda: self.select_file(self.infile_value))
        self.infile_button.grid(row=15, column=2, sticky='WE', padx=5, pady=(5))

        self.infile = tk.Entry(self, textvariable=self.infile_value, width=70)
        self.infile.grid(row=15, column=1, sticky='W', padx=5)

        self.crf = tk.Scale(self, from_=0, to=51, orient=tk.HORIZONTAL, label="Video CRF")
        self.crf.grid(row=16, column=1, sticky='WE', pady=5, padx=5)
        self.crf.set(18)

        self.abr = tk.Scale(self, from_=0, to=384, orient=tk.HORIZONTAL, label="Audio ABR", resolution=10)
        self.abr.grid(row=17, column=1, sticky='WE', pady=5, padx=5)
        self.abr.set(384)

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
        #self.avisynth_extras.insert(tk.END, "Just a text Widget\nin two lines\n")

        self.run = tk.Button(self, text="Run", fg="green", command=lambda: self.runfx())
        self.run.grid(row=20, column=1, sticky='WE', padx=5)

        self.stop = tk.Button(self, text="Stop", fg="red", command=lambda: self.stop_process(True))
        self.stop.grid(row=21, column=1, sticky='WE', padx=5)

        self.quit = tk.Button(self, text="Quit", fg="red", command=self.exit)
        self.quit.grid(row=22, column=1, sticky='WE', padx=5, pady=(0,5))


if __name__ == "__main__":
    root = tk.Tk()
    root.wm_title("videer")
    #operations = Operations()

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