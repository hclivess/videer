import glob
import os

old_files = glob.glob("*.mp4_24libx265_aac128.mp4")
print(old_files)

for old_file in old_files:
    new_name = old_file.replace(".mp4_24libx265_aac128", "")
    os.rename(old_file, new_name)
