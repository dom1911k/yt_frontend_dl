# YT Downloader

A simple one-window Windows app for downloading videos or audio with **yt-dlp**.
Everything is in one file: `yt_downloader.py`.

## Easiest: just download the .exe

1. Open the **Releases** page of this repository (right side of the GitHub page) and click **YT Downloader (latest build)**.
2. Download **YT Downloader.exe** and double-click it. No Python needed.

The .exe is built automatically by GitHub (see `.github/workflows/build-exe.yml`) every time the code changes.
It looks for `yt-dlp.exe` and `ffmpeg.exe` in `C:\Users\<you>`, next to the .exe, or wherever you set them in **Settings...**.

**Updates:** the app checks for a new version each time it starts (and via **Check for app update**).
If one is available it asks you, downloads it, and restarts by itself. You don't have to download it again.

The steps below are only needed if you want to run or build it yourself.

## Step 1: Install Python (one time only)

1. Go to <https://www.python.org/downloads/> and click **Download Python 3.x**.
2. Run the installer. **On the first screen, tick "Add python.exe to PATH"**, then click **Install Now**.
   (tkinter, which the app needs, is included automatically.)

## Step 2: Put the app somewhere

1. Make a folder, for example `C:\Users\domin\YT Downloader`.
2. Save `yt_downloader.py` into that folder.

## Step 3: Run it

1. Open the folder in File Explorer.
2. Click in the address bar at the top, type `cmd`, and press **Enter**. A black window opens in that folder.
3. Type this and press **Enter**:

   ```
   py yt_downloader.py
   ```

The app window opens. The first time, click **Settings...** and check:

- **yt-dlp.exe** → `C:\Users\domin\yt-dlp.exe`
- **ffmpeg folder** → `C:\Users\domin` (the folder that contains `ffmpeg.exe` and `ffprobe.exe`)

These are already the defaults, so normally you just click **Save**. Your choices are remembered in
`yt_downloader_config.json` next to the app.

## Using it

1. Copy a link in your browser, then click **Paste**.
2. Choose **Video** or **Audio only** and the format and quality.
3. Tick **Download whole playlist** only if you want every video in a playlist.
4. Click **Download**. The progress bar shows how far it is; the box below shows yt-dlp's messages.
5. When it's done, click **Open folder**.

**Queue:** click **Add to queue** for each link (or paste several links at once), then click **Download**.
They download one after another. You can also add more links while a download is running.

If downloads start failing (for example "error 403"), click **Update yt-dlp**.

## Step 4 (optional): Make a double-clickable .exe

1. In the same black `cmd` window (in the app's folder), type and press **Enter**:

   ```
   py -m pip install pyinstaller
   ```

2. Then type and press **Enter**:

   ```
   py -m PyInstaller --onefile --windowed --name "YT Downloader" yt_downloader.py
   ```

3. When it finishes, open the new **dist** folder. `YT Downloader.exe` is your app.
   Move it anywhere you like (e.g. your Desktop or its own folder) and double-click it.
   You can delete the `build` folder and the `.spec` file.

The settings file is saved next to the .exe, so put the .exe in a normal folder like Desktop or Documents
(not `C:\Program Files`, where Windows won't let it save settings).

**Note:** Windows Defender SmartScreen may warn you the first time you open a self-made .exe
("Windows protected your PC"). Click **More info → Run anyway**.

## What the options do (yt-dlp flags)

| Choice | yt-dlp flags |
|---|---|
| Video Best | `-f "bv*+ba/b"` |
| Video 1080p / 720p / 480p | `-f "bv*[height<=720]+ba/b[height<=720]"` (example for 720p) |
| MP4 / MKV | `--merge-output-format mp4` (MP4 also prefers MP4-friendly streams: `-S res,ext:mp4:m4a`) |
| Audio | `-x --audio-format mp3` (or m4a/opus/wav) |
| Audio Best / Medium / Small | `--audio-quality 0` / `192K` / `128K` |
| Playlist off / on | `--no-playlist` / `--yes-playlist` |
| Output | `-P "<folder>" -o "%(title)s.%(ext)s"` |
| Extras: cover image / title info | `--embed-thumbnail` / `--embed-metadata` |
| Extras: subtitles (e.g. `en, de`) | `--sub-langs "en.*,de.*" --embed-subs` (audio: separate .srt file) |
| Extras: skip sponsor segments | `--sponsorblock-remove sponsor,selfpromo,interaction` |
| Always | `--ffmpeg-location "<ffmpeg folder>" --newline --no-mtime` |
