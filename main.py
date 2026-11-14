import os
import json
import time
import asyncio
import urllib.parse
import requests
import edge_tts
from moviepy.editor import ImageClip, AudioFileClip, concatenate_videoclips

VOICE = "en-US-AndrewNeural"

async def generate_audio_and_subs(text, audio_path, srt_path):
    communicate = edge_tts.Communicate(text, "en-US-ChristopherNeural")
    submaker = edge_tts.SubMaker()
    with open(audio_path, "wb") as audio_file:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_file.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                submaker.feed(chunk)
    with open(srt_path, "w", encoding="utf-8") as srt_file:
        srt_file.write(submaker.get_srt())
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
    # Smooth Ken Burns zoom-in effect
    zoomed = clip.resize(lambda t: 1 + 0.12 * (t / duration))
    final_clip = zoomed.crop(
        x_center=w / 2,
        y_center=h / 2,
        width=1080,
        height=1920
    )
    return final_clip

def main():
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    with open(event_path, "r", encoding="utf-8") as f:
        event_data = json.load(f)

    payload = event_data.get("client_payload", {})
    script = payload.get("script", "Your brain remembers negative events five times stronger than positive ones.")
    prompts = payload.get("prompts", ["glowing human brain in dark room", "person looking in mirror deep thought"])
    title = payload.get("title", "Psychology Fact You Didn't Know")
    description = payload.get("description", "Decode your mind every day.")
    webhook_url = payload.get("webhook_url", "")

    if isinstance(prompts, str):
        prompts = [p.strip() for p in prompts.split("|") if p.strip()]

    print("1. Generating Voiceover & Subtitles...")
    asyncio.run(generate_audio_and_subs(script, "voice.mp3", "subs.srt"))

    audio = AudioFileClip("voice.mp3")
    total_duration = audio.duration
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

    print("4. Burning Captions with FFmpeg...")
    subtitle_style = "Alignment=10,FontName=Arial,FontSize=18,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=1,Bold=1"
    os.system(f'ffmpeg -y -i raw_video.mp4 -vf "subtitles=subs.srt:force_style=\'{subtitle_style}\'" -c:a copy final_video.mp4')

    if webhook_url:
        print("5. Sending Video to Make.com...")
        with open("final_video.mp4", "rb") as vf:
            requests.post(
                webhook_url,
                data={"title": title, "description": description},
                files={"video": ("final_video.mp4", vf, "video/mp4")},
                timeout=120
            )
    print("Done!")

if __name__ == "__main__":
    main()
