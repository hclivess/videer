import tkinter as tk
import os

class CreateAvs():
    def __init__(self, infile):
        with open("parameters.avs", "w") as avsfile:
            avsfile.write(f'AVISource("{infile}", audio=true)')
            avsfile.write('\n')
            avsfile.write('ConvertToYV24(matrix="rec709")')

            if app.deinterlace_var.get():
                avsfile.write('\n')
                avsfile.write('QTGMC(Preset="Slower")')

class Application(tk.Frame):
    def __init__(self, master=None):

        super().__init__(master)
        self.master = master

        self.grid()
        self.create_widgets()

    @staticmethod
    def runfx(codec, preset, crf, outfile):
        avs = CreateAvs(infile="test.avi")

        os.system("notepad")
        print(f'ffmpeg.exe" -hide_banner -i "parameters.avs" -y -c:v lib{codec} -preset {preset} -crf {crf} -c:a aac -b:a 384k -movflags +faststart -bf 2 -flags +cgop -pix_fmt yuv420p -f mp4 "{outfile}"')

    def create_widgets(self):

        self.deinterlace_var = tk.BooleanVar()
        self.deinterlace_var.set(False)
        self.deinterlace_button = tk.Checkbutton(self, text="Deinterlace", variable=self.deinterlace_var)
        self.deinterlace_button.grid(row=0, column=1, sticky='w', pady=5, padx=5)

        self.codec_label = tk.Label(self)
        self.codec_label["text"] = "Codec: "
        self.codec_var = tk.StringVar()
        self.codec_var.set("x265")
        self.codec_label.grid(row=2, column=0, sticky='', pady=5, padx=5)

        self.codec_button = tk.Radiobutton(self, text="x264", variable=self.codec_var, value="x264")
        self.codec_button.grid(row=2, column=1, sticky='w', pady=5, padx=5)
        self.codec_button = tk.Radiobutton(self, text="x265", variable=self.codec_var, value="x265")
        self.codec_button.grid(row=3, column=1, sticky='w', pady=5, padx=5)

        self.preset_label = tk.Label(self)
        self.preset_label["text"] = "preset: "
        self.preset_var = tk.StringVar()
        self.preset_var.set("normal")
        self.preset_label.grid(row=4, column=0, sticky='', pady=5, padx=5)

        self.preset_button = tk.Radiobutton(self, text="slowest", variable=self.preset_var, value="slowest")
        self.preset_button.grid(row=4, column=1, sticky='w', pady=5, padx=5)
        self.preset_button = tk.Radiobutton(self, text="slower", variable=self.preset_var, value="slower")
        self.preset_button.grid(row=5, column=1, sticky='w', pady=5, padx=5)
        self.preset_button = tk.Radiobutton(self, text="slow", variable=self.preset_var, value="slow")
        self.preset_button.grid(row=6, column=1, sticky='w', pady=5, padx=5)
        self.preset_button = tk.Radiobutton(self, text="normal", variable=self.preset_var, value="normal")
        self.preset_button.grid(row=7, column=1, sticky='w', pady=5, padx=5)
        self.preset_button = tk.Radiobutton(self, text="faster", variable=self.preset_var, value="faster")
        self.preset_button.grid(row=8, column=1, sticky='w', pady=5, padx=5)
        self.preset_button = tk.Radiobutton(self, text="fast", variable=self.preset_var, value="fast")
        self.preset_button.grid(row=9, column=1, sticky='w', pady=5, padx=5)
        self.preset_button = tk.Radiobutton(self, text="fastest", variable=self.preset_var, value="fastest")
        self.preset_button.grid(row=10, column=1, sticky='w', pady=5, padx=5)


        self.infile_label = tk.Label(self)
        self.infile_label["text"] = "Input file: "
        self.infile_label.grid(row=10, column=0, sticky='', pady=5, padx=5)
        self.infile_value = tk.StringVar()
        self.infile_value.set("c:\\test.avi")
        self.infile = tk.Entry(self, textvariable=self.infile_value, width=70)
        self.infile.grid(row=11, column=1, sticky='W', padx=5)

        self.outfile_label = tk.Label(self)
        self.outfile_label["text"] = "Output file: "
        self.outfile_label.grid(row=11, column=0, sticky='', pady=5, padx=5)
        self.outfile_value = tk.StringVar()
        self.outfile_value.set("c:\\test.mp4")
        self.outfile = tk.Entry(self, textvariable=self.outfile_value, width=70)
        self.outfile.grid(row=12, column=1, sticky='W', padx=5)

        self.crf_label = tk.Label(self)
        self.crf_label["text"] = "CRF : "
        self.crf_label.grid(row=13, column=0, sticky='', padx=5)

        self.crf = tk.Scale(self, from_=0, to=51, orient=tk.HORIZONTAL)
        self.crf.grid(row=13, column=1, sticky='W', pady=5, padx=5)
        self.crf.set(18)

        self.extras_label = tk.Label(self)
        self.extras_label["text"] = "Extras commands: "
        self.extras_label.grid(row=14, column=0, sticky='', padx=5)
        self.extras_value = tk.StringVar()
        self.extras_value.set("")
        self.extras = tk.Entry(self, textvariable=self.extras_value, width=10)
        self.extras.grid(row=14, column=1, sticky='W', pady=5, padx=5)

        self.run = tk.Button(self, text="Run", fg="green", command=lambda: self.runfx(self.codec_var.get(), self.preset_var.get(), self.crf.get(), self.outfile.get()))
        self.run.grid(row=15, column=1, sticky='WE', padx=5)

        self.quit = tk.Button(self, text="Quit", fg="red", command=self.master.destroy)
        self.quit.grid(row=16, column=1, sticky='WE', padx=5, pady=(5))

if __name__ == "__main__":
    root = tk.Tk()
    root.wm_title("videer")
    #operations = Operations()

    app = Application(master=root)
    app.mainloop()