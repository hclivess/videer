import tkinter as tk
from tkinter import ttk
from application import Application

if __name__ == "__main__":
    root = tk.Tk()
    root.iconbitmap("icon.ico")
    style = ttk.Style()
    style.configure('W.TButton',
                    font=('calibri', 10, 'bold'),
                    foreground='black')

    root.wm_title("videer")
    root.resizable(False, False)

    app = Application(master=root)
    app.mainloop()