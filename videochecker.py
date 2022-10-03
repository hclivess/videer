import subprocess
import os
import sys

files = sys.argv[1:]
# files = ["a.mkv"]

for file in files:

    command_line = f'ffmpeg -v error -i "{file}" -map 0:v -map 0:a -map 0:s? -c:s copy -vcodec copy -acodec copy -f null -'
    base_name = os.path.splitext(file)[0]
    extension = os.path.splitext(file)[1]

    process = subprocess.Popen(command_line, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    stdout, stderr = process.communicate()
    return_code = process.returncode
    pid = process.pid

    print(f"Base: {base_name}")
    print(f"Extension: {extension}")
    print(f"RC: {return_code}")
    if stdout or stderr:
        print(f"Errors!")
        print(os.path.splitext(file)[1])
        os.rename(file, f"{file}.error")

    process.wait()
