"""
Sequential Video Clip Cutter
=============================
A local Streamlit application that uses FFmpeg/ffprobe via subprocess
to split videos into consecutive, equal-length clips with no gaps and no overlap.
"""

import io
import json
import math
import os
import shutil
import subprocess
import tempfile
import textwrap
import zipfile
from pathlib import Path
from typing import List, Optional, Tuple

import speech_recognition as sr
import streamlit as st

# Automatically ensure FFmpeg and FFprobe binaries are on PATH if static-ffmpeg is installed
try:
    import static_ffmpeg
    static_ffmpeg.add_paths()
except Exception:
    pass


# ==============================================================================
# Helper Functions
# ==============================================================================

def check_dependencies() -> Tuple[bool, Optional[str]]:
    """
    Check if FFmpeg and FFprobe executables are available on the system PATH.
    Returns (True, None) if both exist, or (False, error_message) if missing.
    """
    ffmpeg_path = shutil.which("ffmpeg")
    ffprobe_path = shutil.which("ffprobe")

    missing = []
    if not ffmpeg_path:
        missing.append("ffmpeg")
    if not ffprobe_path:
        missing.append("ffprobe")

    if missing:
        msg = (
            f"**Missing dependency:** `{', '.join(missing)}` was not found on your system PATH.\n\n"
            "Please install FFmpeg and ensure both `ffmpeg` and `ffprobe` are added to your PATH:\n\n"
            "- **Windows (winget):** `winget install Gyan.FFmpeg`\n"
            "- **Windows (Chocolatey):** `choco install ffmpeg`\n"
            "- **macOS (Homebrew):** `brew install ffmpeg`\n"
            "- **Linux (Debian/Ubuntu):** `sudo apt update && sudo apt install ffmpeg`\n\n"
            "After installing, restart your terminal / Streamlit server."
        )
        return False, msg

    return True, None


def format_time(seconds: float) -> str:
    """
    Convert a duration in seconds into HH:MM:SS format.
    
    Args:
        seconds: Time in seconds.
        
    Returns:
        Formatted string 'HH:MM:SS'.
    """
    if seconds < 0:
        seconds = 0.0
    total_secs = int(round(seconds))
    hours = total_secs // 3600
    minutes = (total_secs % 3600) // 60
    secs = total_secs % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def get_duration(video_path: str) -> float:
    """
    Extract the total duration of a video file using ffprobe.
    
    Args:
        video_path: Absolute or relative path to the video file.
        
    Returns:
        Duration in seconds as a float.
        
    Raises:
        RuntimeError: If ffprobe fails or the output cannot be parsed.
    """
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "json",
        video_path
    ]

    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False
        )
    except FileNotFoundError:
        raise RuntimeError("ffprobe executable was not found. Please verify FFmpeg is installed.")

    if result.returncode != 0:
        error_msg = result.stderr.strip() or "Unknown error occurred while running ffprobe."
        raise RuntimeError(f"FFprobe failed to inspect file: {error_msg}")

    try:
        data = json.loads(result.stdout)
        duration_str = data.get("format", {}).get("duration")
        if duration_str is None:
            raise ValueError("Duration field missing in ffprobe output.")
        duration = float(duration_str)
        if duration <= 0:
            raise ValueError(f"Invalid duration detected ({duration}s).")
        return duration
    except (json.JSONDecodeError, ValueError) as err:
        raise RuntimeError(f"Unable to parse duration from ffprobe: {err}")


def srt_timestamp(seconds: float) -> str:
    """
    Format seconds into SRT subtitle timestamp (HH:MM:SS,mmm).
    """
    if seconds < 0:
        seconds = 0.0
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    if millis >= 1000:
        millis = 999
    return f"{hrs:02d}:{mins:02d}:{secs:02d},{millis:03d}"


def escape_drawtext(text: str) -> str:
    """
    Escape special characters for the FFmpeg drawtext filter.
    """
    text = text.replace("\\", "\\\\")
    text = text.replace("'", "\\'")
    text = text.replace(":", "\\:")
    text = text.replace("%", "\\%")
    return text


def build_drawtext_filters(
    captions: List[dict],
    font_color: str = "yellow",
    font_size: int = 28,
    position: str = "Bottom"
) -> Optional[str]:
    """
    Construct a chained FFmpeg drawtext video filter string from a list of timed captions.
    """
    if not captions:
        return None

    filters = []
    for cap in captions:
        raw_text = cap["text"]
        wrapped = "\n".join(textwrap.wrap(raw_text, width=32))
        esc = escape_drawtext(wrapped)
        st = f"{cap['start']:.2f}"
        en = f"{cap['end']:.2f}"

        if position == "Center":
            y_pos = "(h-text_h)/2"
        else:  # Bottom
            y_pos = "h-text_h-50"

        filters.append(
            f"drawtext=text='{esc}':fontsize={font_size}:fontcolor={font_color}"
            f":bordercolor=black:borderw=3:x=(w-text_w)/2:y={y_pos}"
            f":line_spacing=6:enable='between(t,{st},{en})'"
        )

    return ",".join(filters)


def transcribe_clip_audio(
    wav_path: str,
    language: str = "en-US",
    chunk_len: float = 3.5
) -> Tuple[List[dict], str]:
    """
    Transcribe a WAV audio file in sliding chunks using speech_recognition.
    Returns (captions_list, srt_content_str).
    """
    recognizer = sr.Recognizer()
    recognizer.energy_threshold = 300
    recognizer.dynamic_energy_threshold = True

    captions = []
    try:
        with sr.AudioFile(wav_path) as source:
            total_duration = getattr(source, "DURATION", None)
            if not total_duration:
                total_duration = 0.0

            current_time = 0.0
            while current_time < total_duration:
                dur = min(chunk_len, total_duration - current_time)
                if dur < 0.5:
                    break
                try:
                    audio_data = recognizer.record(source, duration=dur)
                    text = recognizer.recognize_google(audio_data, language=language)
                    if text and text.strip():
                        captions.append({
                            "start": current_time,
                            "end": current_time + dur,
                            "text": text.strip()
                        })
                except (sr.UnknownValueError, sr.RequestError):
                    pass
                current_time += dur
    except Exception:
        pass

    # Build standard SRT string
    srt_lines = []
    for idx, c in enumerate(captions, 1):
        srt_lines.append(str(idx))
        srt_lines.append(f"{srt_timestamp(c['start'])} --> {srt_timestamp(c['end'])}")
        srt_lines.append(c["text"])
        srt_lines.append("")

    return captions, "\n".join(srt_lines)


def cut_clip(
    input_path: str,
    output_path: str,
    start: float,
    duration: float,
    fast_mode: bool = False,
    caption_filters: Optional[str] = None
) -> Tuple[bool, str]:
    """
    Slice a segment of a video using FFmpeg, optionally burning captions.
    
    Args:
        input_path: Path to the source video.
        output_path: Destination path for the sliced clip.
        start: Start timestamp in seconds.
        duration: Duration of the clip in seconds.
        fast_mode: If True (and no captions), uses '-c copy' (stream copy).
        caption_filters: Optional FFmpeg drawtext filter string to burn captions.
                   
    Returns:
        Tuple of (success: bool, stderr_output: str).
    """
    # Precision formatted timestamps
    start_str = f"{start:.3f}"
    duration_str = f"{duration:.3f}"

    base_cmd = [
        "ffmpeg",
        "-y",
        "-ss", start_str,
        "-i", input_path,
        "-t", duration_str,
    ]

    if caption_filters:
        # Burning captions requires video filter and re-encoding
        encoding_args = [
            "-vf", caption_filters,
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "20",
            "-c:a", "aac"
        ]
    elif fast_mode:
        # Stream copy: extremely fast, less accurate cut points on keyframes
        encoding_args = ["-c", "copy"]
    else:
        # Frame-accurate cut with libx264 and aac
        encoding_args = [
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "20",
            "-c:a", "aac"
        ]

    post_args = [
        "-avoid_negative_ts", "make_zero",
        "-movflags", "+faststart",
        output_path
    ]

    cmd = base_cmd + encoding_args + post_args

    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False
        )
        if result.returncode == 0:
            return True, ""
        return False, result.stderr.strip()
    except Exception as err:
        return False, str(err)


def build_zip(clip_paths: List[str], srt_paths: Optional[List[str]] = None) -> bytes:
    """
    Pack all sliced clips and optional subtitle files into an in-memory ZIP archive.
    
    Args:
        clip_paths: List of filepaths to the generated video clips.
        srt_paths: Optional list of filepaths to generated .srt files.
        
    Returns:
        Raw bytes representing the 'clips.zip' archive.
    """
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for file_path in clip_paths:
            if file_path and os.path.exists(file_path):
                arcname = os.path.basename(file_path)
                zip_file.write(file_path, arcname=arcname)
        if srt_paths:
            for srt_path in srt_paths:
                if srt_path and os.path.exists(srt_path):
                    arcname = os.path.basename(srt_path)
                    zip_file.write(srt_path, arcname=arcname)
    return zip_buffer.getvalue()


def open_folder(folder_path: str):
    """
    Open the given directory in the native file explorer (Windows / macOS / Linux).
    """
    try:
        norm_path = os.path.normpath(folder_path)
        if os.name == "nt":
            os.startfile(norm_path)
        else:
            subprocess.run(["xdg-open", norm_path], check=False)
    except Exception as err:
        st.warning(f"Could not automatically open folder: {err}")


def get_session_temp_dir() -> str:
    """
    Retrieve or create a temporary directory specific to the current session.
    """
    if "temp_dir" not in st.session_state or not os.path.exists(st.session_state.temp_dir):
        st.session_state.temp_dir = tempfile.mkdtemp(prefix="clipcut_")
    return st.session_state.temp_dir


def cleanup_session_data():
    """
    Delete temporary video files and reset processing results in session state.
    """
    if "temp_dir" in st.session_state and os.path.exists(st.session_state.temp_dir):
        try:
            shutil.rmtree(st.session_state.temp_dir, ignore_errors=True)
        except Exception:
            pass

    keys_to_clear = [
        "temp_dir",
        "clips",
        "zip_data",
        "video_duration",
        "uploaded_file_id",
        "input_video_path"
    ]
    for key in keys_to_clear:
        if key in st.session_state:
            del st.session_state[key]


# ==============================================================================
# Streamlit Application
# ==============================================================================

def main():
    st.set_page_config(
        page_title="Sequential Video Clip Cutter",
        page_icon="🎬",
        layout="wide"
    )

    st.title("🎬 Sequential Video Clip Cutter")
    st.caption("Slice videos into consecutive, equal-length segments with zero gaps and no overlap.")

    # 1. Check FFmpeg / FFprobe installation
    dependencies_ok, dep_error_msg = check_dependencies()
    if not dependencies_ok:
        st.error(dep_error_msg)
        st.stop()

    # Sidebar: Instructions, Upload notes, and Reset button
    with st.sidebar:
        st.header("⚙️ Controls & Settings")

        st.info(
            "📌 **Unlimited Uploads Configured**\n\n"
            "Upload limit is configured to **1,000,000 MB (~1 TB)** in `.streamlit/config.toml`:\n\n"
            "```toml\n"
            "[server]\n"
            "maxUploadSize = 1000000\n"
            "```\n"
            "You can also use the **Direct Local File** tab to process large videos instantly with zero upload overhead!"
        )

        st.markdown("---")

        if st.button("🔄 Reset App & Clear Cache", use_container_width=True, type="secondary"):
            cleanup_session_data()
            st.rerun()

        st.markdown("---")
        st.markdown(
            "**Output format:**\n"
            "- Files named `1 clip.mp4`, `2 clip.mp4`, etc.\n"
            "- Download individually or bundled into `clips.zip`."
        )

    # Initialize session state for clips if not present
    if "clips" not in st.session_state:
        st.session_state.clips = []
    if "zip_data" not in st.session_state:
        st.session_state.zip_data = None

    # Step 1: Upload or Select Video
    st.subheader("1. Select Video Source")

    source_tab1, source_tab2 = st.tabs([
        "📤 Upload Video File (Up to 1 TB / No Limit)",
        "📁 Direct Local File Path (Instant / Zero Upload)"
    ])

    video_path = None
    video_display_name = ""
    video_size_mb = 0.0

    with source_tab1:
        uploaded_file = st.file_uploader(
            "Upload a video file (.mp4, .mov, .mkv, .webm)",
            type=["mp4", "mov", "mkv", "webm"],
            help="Upload the video you want to slice. Configured for unlimited file sizes (up to 1 TB)."
        )

    with source_tab2:
        st.caption("⚡ For huge files (10GB+), enter the file path on your PC to slice immediately with zero upload waiting time:")
        local_input_file = st.text_input(
            "Local Video Path:",
            placeholder="e.g. D:\\Videos\\my_movie.mp4",
            help="Full path to a video file already located on your computer."
        )

    # Determine which input method is active
    if uploaded_file is not None:
        current_file_id = f"upload_{uploaded_file.name}_{uploaded_file.size}"
        if st.session_state.get("uploaded_file_id") != current_file_id:
            cleanup_session_data()
            st.session_state.uploaded_file_id = current_file_id

            # Save uploaded file to temp directory
            temp_dir = get_session_temp_dir()
            file_ext = Path(uploaded_file.name).suffix or ".mp4"
            saved_input_path = os.path.join(temp_dir, f"source_input{file_ext}")

            with open(saved_input_path, "wb") as f:
                f.write(uploaded_file.getbuffer())

            st.session_state.input_video_path = saved_input_path

            try:
                duration = get_duration(saved_input_path)
                st.session_state.video_duration = duration
            except Exception as err:
                st.error(f"Failed to read video file metadata: {err}")
                return

        video_path = st.session_state.get("input_video_path")
        video_display_name = uploaded_file.name
        video_size_mb = uploaded_file.size / (1024 * 1024)

    elif local_input_file.strip():
        cleaned_path = os.path.abspath(local_input_file.strip().strip('"').strip("'"))
        if not os.path.exists(cleaned_path):
            st.error(f"File not found: `{cleaned_path}`")
            return
        if not os.path.isfile(cleaned_path):
            st.error(f"The specified path is a directory, not a file: `{cleaned_path}`")
            return

        current_file_id = f"local_{cleaned_path}_{os.path.getsize(cleaned_path)}"
        if st.session_state.get("uploaded_file_id") != current_file_id:
            cleanup_session_data()
            st.session_state.uploaded_file_id = current_file_id
            st.session_state.input_video_path = cleaned_path

            try:
                duration = get_duration(cleaned_path)
                st.session_state.video_duration = duration
            except Exception as err:
                st.error(f"Failed to read video file metadata: {err}")
                return

        video_path = cleaned_path
        video_display_name = os.path.basename(cleaned_path)
        video_size_mb = os.path.getsize(cleaned_path) / (1024 * 1024)

    else:
        st.info("👆 Please upload a video file or enter a local file path above to get started.")
        if st.session_state.get("uploaded_file_id") is not None:
            cleanup_session_data()
        return

    duration = st.session_state.get("video_duration", 0.0)

    # Preview and Duration display
    col_preview, col_meta = st.columns([3, 2])
    with col_preview:
        if video_path and os.path.exists(video_path):
            st.video(video_path)
    with col_meta:
        st.metric(label="Total Video Duration", value=format_time(duration))
        st.write(f"**Source:** `{video_display_name}`")
        st.write(f"**Raw Duration:** `{duration:.2f} seconds`")
        st.write(f"**Filesize:** `{video_size_mb:.2f} MB`")

    st.markdown("---")

    # Step 2: Segment Length Selection & Validation
    st.subheader("2. Segment Configuration")

    col_val, col_unit = st.columns([2, 1])
    with col_val:
        seg_value = st.number_input(
            "Segment Length",
            min_value=0.1,
            value=1.0,
            step=1.0,
            help="Length of each consecutive clip."
        )
    with col_unit:
        unit = st.selectbox(
            "Unit",
            options=["Minutes", "Seconds"],
            index=0,
            help="Choose whether the segment length is specified in Minutes or Seconds."
        )

    # Calculate segment length in seconds
    segment_seconds = seg_value * 60.0 if unit == "Minutes" else float(seg_value)

    # Validation & Live estimation
    is_valid_segment = True
    validation_error = None

    if segment_seconds <= 0:
        is_valid_segment = False
        validation_error = "Segment length must be greater than 0 seconds."
    elif segment_seconds > duration:
        is_valid_segment = False
        validation_error = (
            f"Segment length ({format_time(segment_seconds)}) cannot exceed "
            f"the total video duration ({format_time(duration)})."
        )

    if is_valid_segment:
        estimated_clips = math.ceil(duration / segment_seconds)
        st.success(
            f"📊 **Live Estimate:** Cutting into **{estimated_clips}** clip(s) "
            f"of ~{seg_value:g} {unit.lower()} each. "
            f"(The final clip will capture the remaining time)."
        )
    else:
        st.warning(f"⚠️ {validation_error}")

    # Slicing mode option
    col_opt1, col_opt2 = st.columns([1, 1])
    with col_opt1:
        fast_mode = st.checkbox(
            "⚡ Fast mode (stream copy, less accurate)",
            value=False,
            help="Uses '-c copy'. Much faster processing without re-encoding, "
                 "but cuts may only snap to the nearest keyframe."
        )
    with col_opt2:
        save_to_local = st.checkbox(
            "💾 Save directly to local folder",
            value=True,
            help="Saves the cut clips directly to a permanent folder on your computer."
        )

    local_dir_path = ""
    if save_to_local:
        default_dir = os.path.join(os.getcwd(), "output_clips")
        local_dir_path = st.text_input(
            "Local output directory:",
            value=st.session_state.get("saved_local_dir", default_dir),
            help="Full directory path where clips will be stored on your PC."
        )

    # Auto-Caption Configuration
    st.markdown("##### 💬 Auto-Captions (Speech-to-Text)")
    enable_captions = st.checkbox(
        "Generate Auto-Captions for clips",
        value=True,
        help="Listens to audio in each clip, automatically transcribes speech, and creates synchronized captions."
    )

    caption_lang = "en-US"
    burn_captions = True
    caption_color = "yellow"
    caption_size = 28
    caption_pos = "Bottom"

    if enable_captions:
        cap_c1, cap_c2, cap_c3 = st.columns([1, 1, 1])
        with cap_c1:
            caption_lang = st.selectbox(
                "Spoken Language",
                options=[
                    ("English (US)", "en-US"),
                    ("English (UK)", "en-GB"),
                    ("English (India)", "en-IN"),
                    ("Hindi", "hi-IN"),
                    ("Spanish", "es-ES"),
                    ("French", "fr-FR"),
                    ("German", "de-DE"),
                    ("Portuguese (Brazil)", "pt-BR"),
                    ("Arabic", "ar-SA"),
                    ("Japanese", "ja-JP")
                ],
                format_func=lambda x: x[0],
                index=0,
                help="Language spoken in the video audio track."
            )[1]
        with cap_c2:
            burn_captions = st.checkbox(
                "🔥 Burn captions onto video (Reels/Shorts style)",
                value=True,
                help="Permanently embeds animated/timed subtitles directly onto video frames."
            )
        with cap_c3:
            caption_color = st.selectbox(
                "Caption Text Color",
                options=["yellow", "white", "cyan", "green"],
                index=0,
                format_func=lambda c: c.capitalize()
            )

        if burn_captions:
            cap_c4, cap_c5 = st.columns([1, 1])
            with cap_c4:
                caption_size = st.slider("Caption Font Size", min_value=18, max_value=48, value=28, step=2)
            with cap_c5:
                caption_pos = st.selectbox("Position", options=["Bottom", "Center"], index=0)

    st.markdown("---")

    # Step 3 & 4: Start Slicing
    st.subheader("3. Process & Slice")

    slice_button = st.button(
        "🚀 Start Slicing Video",
        type="primary",
        disabled=not is_valid_segment,
        use_container_width=True
    )

    if slice_button:
        # Determine target output directory
        temp_dir = get_session_temp_dir()
        if save_to_local and local_dir_path.strip():
            clips_output_dir = os.path.abspath(local_dir_path.strip())
            os.makedirs(clips_output_dir, exist_ok=True)
            st.session_state.saved_local_dir = clips_output_dir
        else:
            clips_output_dir = os.path.join(temp_dir, "clips")
            if os.path.exists(clips_output_dir):
                shutil.rmtree(clips_output_dir, ignore_errors=True)
            os.makedirs(clips_output_dir, exist_ok=True)
            st.session_state.saved_local_dir = None

        st.session_state.clips = []
        st.session_state.zip_data = None

        # Sequential slicing using a running pointer
        start = 0.0
        clip_index = 1
        generated_clips = []
        total_estimated = math.ceil(duration / segment_seconds)

        progress_bar = st.progress(0.0)
        status_text = st.empty()

        processing_failed = False

        while start < duration:
            end = min(start + segment_seconds, duration)
            clip_dur = end - start

            # Skip clips shorter than 0.1s
            if clip_dur < 0.1:
                break

            filename = f"{clip_index} clip.mp4"
            output_path = os.path.join(clips_output_dir, filename)

            status_text.text(
                f"Processing clip {clip_index} of ~{total_estimated} "
                f"[{format_time(start)} ➔ {format_time(end)}]..."
            )

            clip_captions = []
            srt_content = ""
            caption_filter_str = None
            srt_output_path = None

            # Audio extraction and Auto-Captioning
            if enable_captions:
                status_text.text(
                    f"🎤 Transcribing audio for clip {clip_index} of ~{total_estimated} "
                    f"[{format_time(start)} ➔ {format_time(end)}]..."
                )
                wav_temp = os.path.join(temp_dir, f"temp_audio_{clip_index}.wav")
                extract_cmd = [
                    "ffmpeg", "-y",
                    "-ss", f"{start:.3f}",
                    "-i", video_path,
                    "-t", f"{clip_dur:.3f}",
                    "-vn",
                    "-acodec", "pcm_s16le",
                    "-ar", "16000",
                    "-ac", "1",
                    wav_temp
                ]
                subprocess.run(extract_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)

                if os.path.exists(wav_temp):
                    clip_captions, srt_content = transcribe_clip_audio(
                        wav_path=wav_temp,
                        language=caption_lang
                    )
                    if clip_captions:
                        srt_filename = f"{clip_index} clip.srt"
                        srt_output_path = os.path.join(clips_output_dir, srt_filename)
                        try:
                            with open(srt_output_path, "w", encoding="utf-8") as f:
                                f.write(srt_content)
                        except Exception:
                            pass

                        if burn_captions:
                            caption_filter_str = build_drawtext_filters(
                                captions=clip_captions,
                                font_color=caption_color,
                                font_size=caption_size,
                                position=caption_pos
                            )

            # Perform cut with optional burned-in captions
            use_fast = fast_mode and (caption_filter_str is None)
            success, err_log = cut_clip(
                input_path=video_path,
                output_path=output_path,
                start=start,
                duration=clip_dur,
                fast_mode=use_fast,
                caption_filters=caption_filter_str
            )

            if not success:
                st.error(f"❌ Error occurred while generating `{filename}`!")
                if err_log:
                    st.code(err_log, language="bash")
                processing_failed = True
                break

            generated_clips.append({
                "index": clip_index,
                "filename": filename,
                "path": output_path,
                "start": start,
                "end": end,
                "start_str": format_time(start),
                "end_str": format_time(end),
                "duration": clip_dur,
                "captions": clip_captions,
                "srt_path": srt_output_path,
                "srt_content": srt_content
            })

            clip_index += 1
            start = end

            # Update progress
            progress_val = min(start / duration, 1.0)
            progress_bar.progress(progress_val)

        if not processing_failed:
            status_text.text("✅ All clips cut successfully!")
            st.session_state.clips = generated_clips
            # Pre-generate zip bytes containing both MP4 and SRT files
            clip_paths = [c["path"] for c in generated_clips]
            srt_paths = [c["srt_path"] for c in generated_clips if c.get("srt_path")]
            st.session_state.zip_data = build_zip(clip_paths, srt_paths)
            st.success(f"Processing complete! Created {len(generated_clips)} clip(s).")
            st.rerun()

    # Step 5: Display Results & Downloads
    if st.session_state.clips:
        st.markdown("---")
        st.subheader("4. Generated Clips & Download")

        total_clips = len(st.session_state.clips)
        col_m1, col_m2 = st.columns([1, 2])
        with col_m1:
            st.metric(label="Total Clips Created", value=f"{total_clips}")
        with col_m2:
            if st.session_state.zip_data:
                st.download_button(
                    label="📦 Download all as ZIP (clips & subtitles)",
                    data=st.session_state.zip_data,
                    file_name="clips.zip",
                    mime="application/zip",
                    type="primary",
                    use_container_width=True
                )

        # Show local folder location and Open Folder button
        saved_dir = st.session_state.get("saved_local_dir")
        if saved_dir and os.path.exists(saved_dir):
            c_path, c_btn = st.columns([3, 1])
            with c_path:
                st.success(f"💾 **Clips saved locally on your drive:** `{saved_dir}`")
            with c_btn:
                if st.button("📂 Open Output Folder", use_container_width=True):
                    open_folder(saved_dir)

        st.markdown("#### Individual Clip Previews")

        # Display clips in a responsive grid
        for clip in st.session_state.clips:
            cap_count = len(clip.get("captions", []))
            cap_badge = f" • 💬 {cap_count} subtitle(s)" if cap_count > 0 else ""
            with st.expander(
                f"🎬 Clip {clip['index']}: {clip['filename']} ({clip['start_str']} → {clip['end_str']}){cap_badge}",
                expanded=True
            ):
                c_vid, c_info = st.columns([3, 2])
                with c_vid:
                    if os.path.exists(clip["path"]):
                        st.video(clip["path"])
                    else:
                        st.warning("Video file not found on disk.")
                with c_info:
                    st.write(f"**Filename:** `{clip['filename']}`")
                    st.write(f"**Time Range:** `{clip['start_str']} → {clip['end_str']}`")
                    st.write(f"**Duration:** `{clip['duration']:.2f} seconds`")

                    if os.path.exists(clip["path"]):
                        with open(clip["path"], "rb") as f:
                            clip_bytes = f.read()
                        st.download_button(
                            label=f"⬇️ Download {clip['filename']}",
                            data=clip_bytes,
                            file_name=clip["filename"],
                            mime="video/mp4",
                            key=f"dl_{clip['index']}_{clip['filename']}"
                        )

                    if clip.get("srt_content"):
                        st.download_button(
                            label=f"📄 Download Subtitles ({clip['index']} clip.srt)",
                            data=clip["srt_content"].encode("utf-8"),
                            file_name=f"{clip['index']} clip.srt",
                            mime="text/plain",
                            key=f"dl_srt_{clip['index']}"
                        )

                # Show transcript preview if available
                if clip.get("captions"):
                    st.markdown("##### 📝 Transcribed Captions")
                    for cap in clip["captions"]:
                        st.caption(f"⏱️ **{format_time(cap['start'])} ➔ {format_time(cap['end'])}**: {cap['text']}")


if __name__ == "__main__":
    main()
