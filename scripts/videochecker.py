import subprocess
import os
import sys
import re

def check_video(files):
    if type(files) == str:
        files = [files]

    for file in files:
        print(f"Checking {file}...")
        command_line = f'ffmpeg -v error -i "{file}" -map 0:v -map 0:a? -vcodec copy -acodec copy -f null -'
        base_name = os.path.splitext(file)[0]
        extension = os.path.splitext(file)[1]

        process = subprocess.Popen(command_line, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        stdout, stderr = process.communicate()
        return_code = process.returncode
        pid = process.pid

        print(f"Base: {base_name}")
        print(f"Extension: {extension}")
        print(f"RC: {return_code}")

        if stdout:
            allowed_errs = ["invalid as first byte of an EBML number"]
            stdout_detect = stdout.decode().split("\n")

            for error in allowed_errs:
                if error not in stdout_detect[0] or len(stdout_detect) > 2:
                    print(f"Errors!")
                    print(os.path.splitext(file)[1])
                    os.rename(file, f"{file}.error")
                    with open(f"{file}.error.log", "w") as errfile:
                        if stdout:
                            errfile.write(stdout.decode())
                        if stderr:
                            errfile.write(stderr.decode())
                else:
                    print("Minor problems detected.")
        else:
            print("File OK.")


        process.wait()

if __name__ == "__main__":
    files = sys.argv[1:]
    # files = ["a.mkv"]
    check_video(files)