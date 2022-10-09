ffmpeg -i %1 -map 0:v -map 0:a -map 0:s? -c:s copy -vcodec copy -acodec copy -f null
pause