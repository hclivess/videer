import glob
import os
import subprocess


def swap_container(files):
    if type(files) == str:
        files = [files]

    for file in files:
        print(f"Converting {file}...")
        base_name = os.path.splitext(file)[0]
        extension = os.path.splitext(file)[1]
        command_line = f'ffmpeg -i "{file}" -map 0:v -map 0:a -map 0:s? -vcodec copy -acodec copy -c:s copy -f matroska "{base_name}".mp4'

        process = subprocess.Popen(command_line)
        process.communicate()
        return_code = process.returncode
        pid = process.pid

        print(f"Base: {base_name}")
        print(f"Extension: {extension}")
        print(f"RC: {return_code}")
        if return_code == 1:
            os.rename(file, f"{file}.error")

        process.wait()

        print(f"{file} Converted...")


if __name__ == "__main__":
    files = glob.glob("**/*.mkv", recursive=True)
    swap_container(files)
