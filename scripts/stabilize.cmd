ffmpeg -i %1 -vf vidstabdetect=shakiness=7 -f null -
ffmpeg -i %1 -vf vidstabtransform=smoothing=30:zoom=5:input="transforms.trf" stabilized.mp4
pause
