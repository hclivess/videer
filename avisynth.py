import multiprocessing

class CreateAvs:
    def __init__(self, fileobj, app):
        with open(fileobj.avsfile, "w") as avsfile:
            plugins = [
                "masktools2.dll", "mvtools2.dll", "nnedi3.dll",
                "ffms2.dll", "RgTools.dll", "LSMASHSource.dll"
            ]
            imports = ["QTGMC.avsi", "Zs_RF_Shared.avsi"]

            for plugin in plugins:
                avsfile.write(f'Loadplugin("{app.path}/plugins/{plugin}")\n')

            for imp in imports:
                avsfile.write(f'Import("{app.path}/plugins/{imp}")\n')

            source = (f'FFmpegSource2("{fileobj.filename}", vtrack = -1, atrack = -1)'
                      if app.use_ffms2_var.get() else
                      f'AVISource("{fileobj.filename}", audio=true)')
            avsfile.write(f'{source}\n')

            avsfile.write('SetFilterMTMode("FFVideoSource", 3)\n')
            avsfile.write('ConvertToYV24(matrix="rec709")\n')
            avsfile.write(f'Prefetch({multiprocessing.cpu_count()})\n')

            if app.avisynth_extras.get("1.0", "end-1c").strip():
                avsfile.write(f'{app.avisynth_extras.get("1.0", "end-1c")}\n')

            if app.tff_var.get():
                avsfile.write('AssumeTFF()\n')

            if app.deinterlace_var.get():
                preset = app.preset_get(int(app.speed.get()))
                fps_divisor = ', FPSDivisor=2' if app.reduce_fps_var.get() else ''
                avsfile.write(f'QTGMC(Preset="{preset}"{fps_divisor}, EdiThreads={multiprocessing.cpu_count()})\n')