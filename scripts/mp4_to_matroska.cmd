ffmpeg -i %1 -map 0:v -map 0:a -map 0:s? -c:s srt -vcodec copy -acodec copy -f matroska %1.mkv
pause