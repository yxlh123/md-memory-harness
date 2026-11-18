@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 手机和这台电脑连同一个 WiFi，然后用下面打印的地址打开（带口令的那条）。
python webui.py --lan
pause
