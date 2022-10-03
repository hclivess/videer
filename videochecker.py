import subprocess
import os
import sys
import json

files = sys.argv[1:]
# files = ["a.mkv"]

for file in files:
    print(f"Checking {file}...")
    command_line = f'ffmpeg -v error -i "{file}" -map 0:v -map 0:a -vcodec copy -acodec copy -f null -'
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
        with open(f"{file}.errorlog", "w") as errfile:
            if stdout:
                errfile.write(stdout.decode())
            if stderr:
                errfile.write(stderr.decode())
    else:
        print("File OK.")

    process.wait()
