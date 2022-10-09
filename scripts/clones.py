import glob
import subprocess
import os
import sys
import re

def swap_container(files_mp4, files_mkv):
    for file in files_mp4:
        basename = os.path.splitext(file)[0]
        if f"{basename}.mkv" in files_mkv:
            os.remove(file)
            print(file)
            print(f"{basename}.mkv")



if __name__ == "__main__":
    files_mp4 = glob.glob("**/*.mp4", recursive=True)
    files_mkv = glob.glob("**/*.mkv", recursive=True)
    swap_container(files_mp4, files_mkv)