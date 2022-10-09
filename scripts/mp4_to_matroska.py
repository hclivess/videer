import subprocess
import os
import sys
import re

def swap_container(files):
    if type(files) == str:
        files = [files]

    for file in files:
        print(f"Converting {file}...")
        base_name = os.path.splitext(file)[0]
        extension = os.path.splitext(file)[1]
        command_line = f'ffmpeg -i "{file}" -map 0:v -map 0:a -map 0:s? -vcodec copy -acodec copy -c:s copy -f matroska "{base_name}".mkv'

        process = subprocess.Popen(command_line)
        process.communicate()
        return_code = process.returncode
        pid = process.pid

        print(f"Base: {base_name}")
        print(f"Extension: {extension}")
        print(f"RC: {return_code}")

        process.wait()

        print(f"{file} Converted...")


if __name__ == "__main__":
    files = sys.argv[1:]
    # files = ["a.mkv"]
    swap_container(files)