@echo off
REM 用系统 Python 3.11 的 venv 打包（其 tcl/tk 完整，可正常打 GUI exe）
setlocal
cd /d %~dp0

venv\Scripts\pyinstaller.exe --onefile --windowed --name YouTubeDownloader ^
  --distpath pyout_1 --workpath pybuild_1 --specpath pybuild_1 ^
  --icon "app.ico" ^
  --add-binary "ffmpeg.exe;." ^
  --add-data "app.ico;." ^
  --collect-submodules yt_dlp ^
  yt_downloader.py

echo BUILD_DONE
pause
