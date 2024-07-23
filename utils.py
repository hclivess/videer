import re
import logging

def multiple_replace(string, rep_dict):
    pattern = re.compile("|".join([re.escape(k) for k in sorted(rep_dict, key=len, reverse=True)]), flags=re.DOTALL)
    return pattern.sub(lambda x: rep_dict[x.group(0)], string)

def assemble_final(fileobj, app_gui):
    command = ['ffmpeg.exe -err_detect crccheck+bitstream+buffer -hide_banner']

    if app_gui.codec_var.get() in ["hevc_nvenc", "h264_nvenc"]:
        command.append("-hwaccel cuda")

    input_file = f'-i "{fileobj.avsfile}"' if app_gui.use_avisynth_var.get() else f'-i "{fileobj.filename}"'
    command.append(f'{input_file} -y')

    preset = app_gui.preset_get(int(app_gui.speed.get())).replace(" ", "")
    command.append(f'-preset {preset}')

    command.extend(['-map 0:v', '-map 0:a?', '-map 0:s?'])

    if app_gui.stereo_var.get():
        command.append('-ac 2')

    if app_gui.codec_var.get() == "copy":
        command.append('-c:v copy')
        v_codec_desc = "copy"
        crf_desc = "copy"
    else:
        command.append(f'-c:v {app_gui.codec_var.get()}')
        crf_option = '-cq' if app_gui.codec_var.get() in ["hevc_nvenc", "h264_nvenc"] else '-crf'
        command.append(f'{crf_option} {app_gui.crf.get()}')
        v_codec_desc = app_gui.codec_var.get()
        crf_desc = app_gui.crf.get()

    if app_gui.audio_codec_var.get() == "copy":
        command.append('-c:a copy')
        abr_desc = "copy"
        a_codec_desc = "copy"
    else:
        command.extend([f'-c:a {app_gui.audio_codec_var.get()}', f'-b:a {app_gui.abr.get()}k'])
        abr_desc = f"{app_gui.abr.get()}k"
        a_codec_desc = app_gui.audio_codec_var.get()

    command.append('-c:s copy')

    if app_gui.corrupt_var.get():
        command.append("-bsf:v h264_mp4toannexb")

    command.extend([
        app_gui.extras_value.get(),
        '-metadata comment="Made with Videer https://github.com/hclivess/videer"',
        f'-metadata description="Video Codec: {v_codec_desc}, Preset: {preset}, CRF: {crf_desc}, Audio Codec: {a_codec_desc}, Audio Bitrate: {abr_desc}"',
        '-movflags +faststart',
        '-bf 2',
        '-flags +cgop',
        '-pix_fmt yuv420p',
        f'-f matroska "{fileobj.outputname}"',
        '-y'
    ])

    return " ".join(command)

def get_logger(filename):
    logFormatter = logging.Formatter("%(asctime)s [%(threadName)-12.12s] [%(levelname)-5.5s]  %(message)s")
    rootLogger = logging.getLogger()
    rootLogger.propagate = False
    rootLogger.setLevel(logging.INFO)
    fileHandler = logging.FileHandler(f"{filename}.log")
    fileHandler.setFormatter(logFormatter)
    rootLogger.addHandler(fileHandler)
    consoleHandler = logging.StreamHandler()
    consoleHandler.setFormatter(logFormatter)
    rootLogger.addHandler(consoleHandler)
    return rootLogger