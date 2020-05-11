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

            if app.avisynth_extras.get("1.0",tk.END).strip():
                avsfile.write('\n')
                avsfile.write(app.avisynth_extras.get("1.0",tk.END))

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

        def with_avisynth(file):
            return f'ffmpeg.exe ' \
                   f'-hide_banner ' \
                   f'-i "parameters.avs" -y ' \
                   f'-c:v {self.codec_var.get()} ' \
                   f'-preset {self.preset_var.get()} ' \
                   f'-crf {self.crf.get()} ' \
                   f'-c:a {self.audio_codec_var.get()} ' \
                   f'-b:a {self.abr.get()}k ' \
                   f'-movflags ' \
                   f'+faststart ' \
                   f'-bf 2 ' \
                   f'-flags ' \
                   f'+cgop ' \
                   f'-pix_fmt yuv420p ' \
                   f'-f mp4 "{file}_processed.mp4" ' \
                   f'{self.extras_value.get()}'

        def without_avisynth(file):
            return f'ffmpeg.exe -hide_banner -i "{file}" -y ' \
                   f'-c:v {self.codec_var.get()} ' \
                   f'-preset {self.preset_var.get()} ' \
                   f'-crf {self.crf.get()} ' \
                   f'-c:a {self.audio_codec_var.get()} ' \
                   f'-b:a {self.abr.get()}k ' \
                   f'-movflags ' \
                   f'+faststart ' \
                   f'-bf 2 ' \
                   f'-flags ' \
                   f'+cgop ' \
                   f'-pix_fmt yuv420p ' \
                   f'-f mp4 "{file}_processed.mp4" ' \
                   f'{self.extras_value.get()}'

        for file in files:
            info_box.configure(state='normal')
            info_box.insert(tk.INSERT, f"Processing {file.split('/')[-1]}\n")
            info_box.configure(state='disabled')

            if self.use_avisynth_var.get():
                CreateAvs(infile=file)
                command_line = with_avisynth(file)
            else:
                command_line = without_avisynth(file)

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

    def stop_process(self):
        pipe = subprocess.Popen("Taskkill /IM ffmpeg.exe /F", shell=True, stdout=subprocess.PIPE).stdout
        print("Stop signal sent")
        output = pipe.read().decode()
        if not output:
            output = "No relevant process found"
        messagebox.showinfo(title="Info", message=output)
        pipe.close()

    def create_widgets(self):

        self.use_avisynth_var = tk.BooleanVar()
        self.use_avisynth_var.set(False)
        self.use_avisynth_button = tk.Checkbutton(self, text="Use AviSynth", variable=self.use_avisynth_var)
        self.use_avisynth_button.bind("<Button-1>", self.set_deinterlace_false)
        self.use_avisynth_button.grid(row=1, column=1, sticky='w', pady=5, padx=5)

        self.deinterlace_var = tk.BooleanVar()
        self.deinterlace_var.set(False)
        self.deinterlace_button = tk.Checkbutton(self, text="Deinterlace", variable=self.deinterlace_var)
        self.deinterlace_button.bind("<Button-1>", self.set_avisynth_true)
        self.deinterlace_button.grid(row=0, column=1, sticky='w', pady=5, padx=5)






        self.audio_codec_label = tk.Label(self)
        self.audio_codec_label["text"] = "Audio Codec: "
        self.audio_codec_var = tk.StringVar()
        self.audio_codec_var.set("aac")
        self.audio_codec_label.grid(row=2, column=0, sticky='', pady=5, padx=5)

        self.audio_codec_button = tk.Radiobutton(self, text="LAME MP3", variable=self.audio_codec_var, value="libmp3lame")
        self.audio_codec_button.grid(row=2, column=1, sticky='w', pady=5, padx=5)
        self.audio_codec_button = tk.Radiobutton(self, text="AAC", variable=self.audio_codec_var, value="aac")
        self.audio_codec_button.grid(row=3, column=1, sticky='w', pady=5, padx=5)
        self.audio_codec_button = tk.Radiobutton(self, text="Opus", variable=self.audio_codec_var, value="libopus")
        self.audio_codec_button.grid(row=4, column=1, sticky='w', pady=5, padx=5)






        self.codec_label = tk.Label(self)
        self.codec_label["text"] = "Video Codec: "
        self.codec_var = tk.StringVar()
        self.codec_var.set("libx265")
        self.codec_label.grid(row=5, column=0, sticky='', pady=5, padx=5)

        self.codec_button = tk.Radiobutton(self, text="x264", variable=self.codec_var, value="libx264")
        self.codec_button.grid(row=5, column=1, sticky='w', pady=5, padx=5)
        self.codec_button = tk.Radiobutton(self, text="x265", variable=self.codec_var, value="libx265")
        self.codec_button.grid(row=6, column=1, sticky='w', pady=5, padx=5)
        self.codec_button = tk.Radiobutton(self, text="V9", variable=self.codec_var, value="libvpx-vp9")
        self.codec_button.grid(row=7, column=1, sticky='w', pady=5, padx=5)

        self.preset_label = tk.Label(self)
        self.preset_label["text"] = "Preset: "
        self.preset_var = tk.StringVar()
        self.preset_var.set("medium")
        self.preset_label.grid(row=8, column=0, sticky='', pady=5, padx=5)

        self.preset_button = tk.Radiobutton(self, text="veryslow", variable=self.preset_var, value="veryslow")
        self.preset_button.grid(row=8, column=1, sticky='w', pady=5, padx=5)
        self.preset_button = tk.Radiobutton(self, text="slower", variable=self.preset_var, value="slower")
        self.preset_button.grid(row=9, column=1, sticky='w', pady=5, padx=5)
        self.preset_button = tk.Radiobutton(self, text="slow", variable=self.preset_var, value="slow")
        self.preset_button.grid(row=10, column=1, sticky='w', pady=5, padx=5)
        self.preset_button = tk.Radiobutton(self, text="medium", variable=self.preset_var, value="medium")
        self.preset_button.grid(row=11, column=1, sticky='w', pady=5, padx=5)
        self.preset_button = tk.Radiobutton(self, text="faster", variable=self.preset_var, value="faster")
        self.preset_button.grid(row=12, column=1, sticky='w', pady=5, padx=5)
        self.preset_button = tk.Radiobutton(self, text="fast", variable=self.preset_var, value="fast")
        self.preset_button.grid(row=13, column=1, sticky='w', pady=5, padx=5)
        self.preset_button = tk.Radiobutton(self, text="veryfast", variable=self.preset_var, value="veryfast")
        self.preset_button.grid(row=14, column=1, sticky='w', pady=5, padx=5)

        self.infile_value = tk.StringVar()
        self.infile_value.set("c:/test.avi")

        self.infile_label = tk.Label(self)
        self.infile_label["text"] = "Input File(s): "
        self.infile_label.grid(row=15, column=0, sticky='', pady=5, padx=5)
        self.infile_button = tk.Button(self, text="Select", fg="green", command=lambda: self.select_file(self.infile_value))
        self.infile_button.grid(row=15, column=2, sticky='WE', padx=5, pady=(5))

        self.infile = tk.Entry(self, textvariable=self.infile_value, width=70)
        self.infile.grid(row=15, column=1, sticky='W', padx=5)

        self.crf_label = tk.Label(self)
        self.crf_label["text"] = "CRF: "
        self.crf_label.grid(row=16, column=0, sticky='', padx=5)
        self.crf = tk.Scale(self, from_=0, to=51, orient=tk.HORIZONTAL, width=30)
        self.crf.grid(row=16, column=1, sticky='WE', pady=5, padx=5)
        self.crf.set(18)

        self.abr_label = tk.Label(self)
        self.abr_label["text"] = "Audio ABR: "
        self.abr_label.grid(row=17, column=0, sticky='', padx=5)
        self.abr = tk.Scale(self, from_=0, to=384, orient=tk.HORIZONTAL, width=30)
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
        self.run.grid(row=20, column=1, sticky='WE', padx=5, pady=(5))

        self.stop = tk.Button(self, text="Stop", fg="red", command=self.stop_process)
        self.stop.grid(row=21, column=1, sticky='WE', padx=5, pady=(5))

        self.quit = tk.Button(self, text="Quit", fg="red", command=self.master.destroy)
        self.quit.grid(row=22, column=1, sticky='WE', padx=5, pady=(5))


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