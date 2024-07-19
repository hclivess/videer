import glob
import os
import subprocess


def probe(file):
    command_line = f'ffprobe -v 0 -select_streams v -of default=noprint_wrappers=1:nokey=1 -show_entries stream=r_frame_rate "{file}"'
    process = subprocess.Popen(command_line, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out, err = process.communicate()
    result = out.decode().strip().split("/")
    fps = int(result[0]) / int(result[1])
    process.wait()
    return round(float(fps), 2)


def deframe(files):
    if type(files) == str:
        files = [files]

    for file in files:
        fps = probe(file)

        if fps == 59.94:

            print(f"Converting {file}...")
            base_name = os.path.splitext(file)[0]
            extension = os.path.splitext(file)[1]
            command_line = f'ffmpeg -i "{file}" -filter:v fps=29.97 -map 0:v -map 0:a -map 0:s? -vcodec libx265 -crf 24 -acodec copy -c:s copy -f matroska "{base_name}.temp.mkv"'

            process = subprocess.Popen(command_line)
            process.communicate()
            return_code = process.returncode
            pid = process.pid

            print(f"Base: {base_name}")
            print(f"Extension: {extension}")
            print(f"RC: {return_code}")

            process.wait()

            if return_code == 1:
                os.rename(file, f"{file}.error")
            else:
                os.rename(file, f"{file}.old")
                os.rename(f"{base_name}.temp.mkv", f"{base_name}.mkv")

            print(f"{file} Converted...")
        else:
            print(f"No need to convert {fps} {file}")


if __name__ == "__main__":
    files = glob.glob("**/*.mkv", recursive=True)
    deframe(files)
