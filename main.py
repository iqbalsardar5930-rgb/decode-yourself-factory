import os
import re
import json
import time
import asyncio
import urllib.parse
import requests
import edge_tts
from moviepy.editor import ImageClip, AudioFileClip, concatenate_videoclips

VOICE = "en-US-AndrewNeural"
MAKE_WEBHOOK_URL = "https://hook.eu1.make.com/h3dlw6hnrepqnhk23rt3h99kthjz7m66"

def format_srt_time(seconds):
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 1000)
    return f"{hrs:02d}:{mins:02d}:{secs:02d},{ms:03d}"

def build_fallback_srt(text, total_duration, srt_path):
    words = text.split()
    if not words:
        words = ["Psychology", "Fact"]
    chunk_size = 4
    chunks = [" ".join(words[i:i + chunk_size]) for i in range(0, len(words), chunk_size)]
    step = total_duration / max(len(chunks), 1)
    with open(srt_path, "w", encoding="utf-8") as f:
        for idx, chunk in enumerate(chunks, start=1):
            start_t = format_srt_time((idx - 1) * step)
            end_t = format_srt_time(idx * step)
            f.write(f"{idx}\n{start_t} --> {end_t}\n{chunk}\n\n")

async def generate_audio_and_subs(text, audio_path, srt_path):
    if not text.strip():
        text = "Your brain remembers negative events five times stronger than positive ones."
    communicate = edge_tts.Communicate(text, VOICE)
    submaker = edge_tts.SubMaker()
    has_boundaries = False
    with open(audio_path, "wb") as audio_file:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_file.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                submaker.feed(chunk)
                has_boundaries = True
    if has_boundaries:
        with open(srt_path, "w", encoding="utf-8") as srt_file:
            srt_file.write(submaker.get_srt())
    return has_boundaries

def download_image(prompt, filename):
    encoded_prompt = urllib.parse.quote(prompt + ", cinematic lighting, ultra detailed, 8k, vertical 9:16")
    url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1080&height=1920&nologo=true&model=flux"
    for attempt in range(3):
        try:
            res = requests.get(url, timeout=60)
            if res.status_code == 200 and len(res.content) > 5000:
                with open(filename, "wb") as f:
                    f.write(res.content)
                return True
        except Exception as e:
            print(f"Retry {attempt+1} for {filename}: {e}")
            time.sleep(3)
    return False

def create_zoom_clip(img_path, duration):
    clip = ImageClip(img_path).set_duration(duration)
    w, h = clip.size
    zoomed = clip.resize(lambda t: 1 + 0.12 * (t / duration))
    final_clip = zoomed.crop(
        x_center=w / 2,
        y_center=h / 2,
        width=1080,
        height=1920
    )
    return final_clip

def main():
    parsed = {}
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if event_path and os.path.exists(event_path):
        try:
            with open(event_path, "r", encoding="utf-8") as f:
                event_data = json.load(f)
            payload = event_data.get("client_payload", {})
            ai_raw = payload.get("ai_data", "")
            if isinstance(ai_raw, str) and ai_raw.strip():
                cleaned = re.sub(r"^```(?:json)?|```$", "", ai_raw.strip(), flags=re.MULTILINE).strip()
                parsed = json.loads(cleaned)
            elif isinstance(ai_raw, dict):
                parsed = ai_raw
            else:
                parsed = payload
        except Exception as e:
            print(f"Error parsing event payload: {e}")

    topic = parsed.get("topic", "Psychology Fact")
    script = parsed.get("script", "").strip()
    if not script or len(script) < 3:
        script = "Your brain remembers negative events five times stronger than positive ones."

    prompts = parsed.get("image_prompts", parsed.get("prompts", ["glowing human brain in dark room", "person looking in mirror deep thought"]))
    title = parsed.get("title", "Psychology Fact You Didn't Know #shorts")
    description = parsed.get("description", "Decode your mind every day. #psychology #shorts")
    webhook_url = parsed.get("webhook_url") or MAKE_WEBHOOK_URL

    if isinstance(prompts, str):
        prompts = [p.strip() for p in prompts.split("|") if p.strip()]
    if not prompts:
        prompts = ["glowing human brain in dark room"]

    print("1. Generating Voiceover & Subtitles...")
    try:
        has_subs = asyncio.run(generate_audio_and_subs(script, "voice.mp3", "subs.srt"))
    except Exception as e:
        print(f"TTS Error encountered: {e}. Using fallback script.")
        fallback_script = "Your brain remembers negative events five times stronger than positive ones."
        has_subs = asyncio.run(generate_audio_and_subs(fallback_script, "voice.mp3", "subs.srt"))
        script = fallback_script

    if not os.path.exists("voice.mp3") or os.path.getsize("voice.mp3") < 1000:
        raise RuntimeError("Voice audio file was not generated correctly.")

    audio = AudioFileClip("voice.mp3")
    total_duration = audio.duration
    if not has_subs:
        build_fallback_srt(script, total_duration, "subs.srt")

    clip_duration = total_duration / max(len(prompts), 1)

    print("2. Downloading AI Images...")
    clips = []
    for idx, prompt in enumerate(prompts):
        img_file = f"img_{idx}.jpg"
        if download_image(prompt, img_file):
            clips.append(create_zoom_clip(img_file, clip_duration))

    if not clips:
        raise RuntimeError("No images could be downloaded.")

    print("3. Assembling Video with Zoom...")
    video = concatenate_videoclips(clips, method="compose").set_audio(audio)
    video.write_videofile("raw_video.mp4", fps=24, codec="libx264", audio_codec="aac")

    print("4. Burning Captions with FFmpeg (YouTube Optimized)...")
    subtitle_style = "Alignment=10,FontName=Arial,FontSize=18,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=1,Bold=1"
    os.system(f'ffmpeg -y -i raw_video.mp4 -vf "subtitles=subs.srt:force_style=\'{subtitle_style}\'" -c:v libx264 -preset ultrafast -pix_fmt yuv420p -c:a aac -movflags +faststart final_video.mp4')

    if not os.path.exists("final_video.mp4") or os.path.getsize("final_video.mp4") < 5000:
        raise RuntimeError("Final video generation failed or file is empty.")

    if webhook_url and "YOUR_MAKE" not in webhook_url:
        print("5. Sending Video to Make.com...")
        with open("final_video.mp4", "rb") as vf:
            requests.post(
                webhook_url,
                data={"topic": topic, "title": title, "description": description},
                files={"video": ("final_video.mp4", vf, "video/mp4")},
                timeout=120
            )
    print("Done!")

if __name__ == "__main__":
    main()
