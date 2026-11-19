<<<<<<< HEAD
# Sequential Video Clip Cutter 🎬

A local Streamlit application that uses FFmpeg and ffprobe via `subprocess` to split a video into consecutive, equal-length clips with no gaps and no overlap.

## Features

- **Upload & Inspection:** Supports `.mp4`, `.mov`, `.mkv`, and `.webm` files. Automatically reads duration via `ffprobe` and displays it in `HH:MM:SS`.
- **Flexible Segment Length:** Specify clip lengths in Minutes or Seconds with live clip count estimation.
- **💬 Auto-Captions (Speech-to-Text):** Automatically listens to clip audio, transcribes speech in multiple languages (English, Hindi, Spanish, etc.), and generates synchronized subtitles.
- **🔥 Burn Captions onto Video (Reels/Shorts style):** Customizable font color (Yellow, White, Cyan, Green), font size, and positioning (Bottom, Center) with high-contrast black borders for maximum readability.
- **Subtitles (.srt) Export:** Generates standard `.srt` subtitle files for each clip, available for individual download and packaged into the ZIP archive.
- **Local Drive Saving:** Option to save sliced clips and subtitles directly to a local folder (e.g. `D:\cilpcut automation\output_clips`) with an "Open Output Folder" button for one-click access in File Explorer.
- **Frame-Accurate Slicing:** Uses FFmpeg with `-c:v libx264 -preset veryfast -crf 20 -c:a aac -avoid_negative_ts make_zero -movflags +faststart`.
- **Fast Mode Option:** Includes an optional stream copy mode (`-c copy`) for near-instant cuts on keyframes.
- **Consecutive Pointer Logic:** Slices continuously without gaps or overlaps (`start = 0; while start < duration: end = min(start + seg, duration); cut; start = end`). Clips shorter than 0.1s are cleanly skipped.
- **Standardized Naming:** Clips are named `1 clip.mp4`, `2 clip.mp4`, etc., with total clip count displayed via `st.metric`.
- **Preview & Export:** In-browser previews for each clip, individual download buttons, and a single "Download all as ZIP" (`clips.zip`) button.
- **Robust State Handling:** Sliced clips persist across download clicks in `st.session_state` without triggering unnecessary re-processing.
- **Unlimited File Support:** Pre-configured with `.streamlit/config.toml` (`maxUploadSize = 1000000` / 1 TB) plus a "Direct Local File Path" tab for instant zero-copy processing of huge files.

---

## Prerequisites

Make sure **FFmpeg** and **ffprobe** are installed and accessible in your system's PATH.

### Installing FFmpeg:
- **Windows (winget):**
  ```powershell
  winget install Gyan.FFmpeg
  ```
  *(or download from [gyan.dev/ffmpeg/builds](https://www.gyan.dev/ffmpeg/builds/) and add its `bin` folder to your System PATH)*
- **macOS (Homebrew):**
  ```bash
  brew install ffmpeg
  ```
- **Linux (Debian/Ubuntu):**
  ```bash
  sudo apt update && sudo apt install ffmpeg
  ```

---

## Installation & Running

1. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```
   *(Includes `static-ffmpeg`, which automatically bundles self-contained FFmpeg and ffprobe binaries if not already installed on your system)*

2. **Run the Streamlit app:**
   ```bash
   streamlit run app.py
   ```

---

## Large / Unlimited File Uploads

This project includes `.streamlit/config.toml` configured for practically unlimited uploads:

```toml
[server]
# 1,000,000 MB = 1 Terabyte (practically unlimited)
maxUploadSize = 1000000
```

Additionally, the app includes a **Direct Local File Path** tab where you can enter the path to any video on your hard drive (e.g. `D:\Videos\long_movie.mp4`) to process videos instantly with zero upload time and zero memory overhead!
=======
# cilpcut-automation
this is a video clip cut automation that runs locally
>>>>>>> fb3a025df0fb632a8de91a2d6795be65abc5418d
