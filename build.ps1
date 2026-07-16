maturin build --release
uv pip install (Get-ChildItem .\target\wheels\q565_rust-*-win_amd64.whl).FullName --force-reinstall
pyinstaller --noconfirm --onefile --windowed --icon "./icon.ico" --add-data "./fonts;fonts/" --add-data "./images;images/" --add-data "./SignalRGBPlugin;SignalRGBPlugin/" "./signalrgb.py"