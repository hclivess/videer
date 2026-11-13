#!/bin/bash
# Run inside a container of another distribution, with the unpacked release build mounted at /frozen:
# encode with that distribution's FFmpeg and install VapourSynth from inside the app. The two things that
# worked on the build machine and nowhere newer (issue #21): the bundled libstdc++ reaching the system's
# FFmpeg, and the bundled OpenSSL looking for certificates where only Ubuntu keeps them.
#
#   docker run --rm -v <unpacked>:/frozen:ro -v $PWD/scripts:/scripts:ro fedora:latest bash /scripts/ci_other_distro.sh
set -e
. /etc/os-release
echo "== $PRETTY_NAME"
case "$ID" in
  arch)          pacman -Syu --noconfirm --needed ffmpeg libglvnd libxkbcommon fontconfig dbus >/dev/null ;;
  fedora)        dnf install -y -q ffmpeg-free mesa-libGL mesa-libEGL libxkbcommon fontconfig dbus-libs >/dev/null ;;
  debian|ubuntu) export DEBIAN_FRONTEND=noninteractive
                 apt-get update -qq
                 apt-get install -y -qq --no-install-recommends ffmpeg libgl1 libegl1 libxkbcommon0 libdbus-1-3 libfontconfig1 ca-certificates >/dev/null ;;
  opensuse*)     zypper -n -q install ffmpeg Mesa-libGL1 Mesa-libEGL1 libxkbcommon0 fontconfig libdbus-1-3 >/dev/null ;;
  *)             echo "no package list for $ID"; exit 1 ;;
esac
mkdir -p /opt/app /t
cp -r /frozen/videer-*-linux-x64 /opt/app/
APP=$(ls /opt/app/*/videer)
# Fedora and openSUSE ship an FFmpeg without x264; VP9 is in all of them
codec=$(ffmpeg -hide_banner -encoders 2>/dev/null | grep -q libx264 && echo libx264 || echo libvpx-vp9)
ffmpeg -y -loglevel error -f lavfi -i testsrc=size=320x240:rate=25 -f lavfi -i sine=frequency=440 -t 3 -c:v mpeg4 -c:a aac /t/clip.mp4
QT_QPA_PLATFORM=offscreen VIDEER_SELFTEST=/t/clip.mp4 VIDEER_SELFTEST_SETTINGS="{\"video_codec\": \"$codec\"}" timeout 600 "$APP"
ls -la /t/clip_${codec}_aac_crf30_abr96.mkv
timeout 900 "$APP" --install-vapoursynth
echo "== $PRETTY_NAME: encoded with $codec, VapourSynth installed"
