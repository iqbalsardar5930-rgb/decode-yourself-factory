import os
import re
import json
import time
import asyncio
import urllib.parse
import requests
import edge_tts
from moviepy.editor import ImageClip, AudioFileClip, concatenate_videoclips
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

VOICE = "en-US-AndrewNeural"
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
YT_CLIENT_ID = os.environ.get("YT_CLIENT_ID", "")
YT_CLIENT_SECRET = os.environ.get("YT_CLIENT_SECRET", "")
YT_REFRESH_TOKEN = os.environ.get("YT_REFRESH_TOKEN", "")

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
        text = "Your brain processes thousands of thoughts every single day, shaping your reality without you even realizing it."
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
    url = "https" + f"://image.pollinations.ai/prompt/{encoded_prompt}?width=1080&height=1920&nologo=true&model=flux"
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

def call_gemini_api(topic):
    url = "https" + f"://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    
    prompt = f"""You are an elite viral YouTube Shorts scriptwriter for the channel "Decode Yourself".
Create 1 unique, mind-blowing human psychology fact video script that is at least 60 seconds when spoken (approximately 130 to 150 words) based on this topic: "{topic}".

STRICT RULES:
1. Return ONLY valid raw JSON (no markdown, no ```json tags, no extra text).
2. Use this exact JSON structure:
{{
  "topic": "Short 4-6 word topic name",
  "title": "Catchy viral title under 60 chars #shorts",
  "description": "Engaging 2-line caption with hashtags #psychology #mindset #shorts",
  "script": "Full, detailed spoken voiceover script in simple, punchy English lasting at least 60 seconds. Start with a powerful hook and build deep engagement.",
  "image_prompts": [
    "Vertical 9:16 dark moody cinematic illustration of scene 1, no text",
    "Vertical 9:16 dark moody cinematic illustration of scene 2, no text",
    "Vertical 9:16 dark moody cinematic illustration of scene 3, no text",
    "Vertical 9:16 dark moody cinematic illustration of scene 4, no text"
  ]
}}"""

    headers = {"Content-Type": "application/json"}
    data = {
        "contents": [{
            "parts": [{"text": prompt}]
        }]
    }
    
    try:
        response = requests.post(url, headers=headers, json=data, timeout=60)
        if response.status_code == 200:
            res_json = response.json()
            raw_text = res_json["candidates"][0]["content"]["parts"][0]["text"]
            cleaned = re.sub(r"^```(?:json)?|```$", "", raw_text.strip(), flags=re.MULTILINE).strip()
            return json.loads(cleaned)
    except Exception as e:
        print(f"Gemini API Error: {e}")
    
    return {
        "topic": topic,
        "title": f"{topic} #shorts",
        "description": f"Fascinating psychology fact about {topic}. #psychology #shorts",
        "script": f"Here is an incredible psychological fact about {topic}. Your brain constantly processes information in ways you never realized, shaping your daily decisions, emotional responses, and long-term memory without your conscious awareness.",
        "image_prompts": [
            "Vertical 9:16 dark moody cinematic illustration of human brain glowing",
            "Vertical 9:16 dark moody cinematic illustration of person thinking deeply",
            "Vertical 9:16 dark moody cinematic illustration of neural pathways",
            "Vertical 9:16 dark moody cinematic illustration of calm night mind"
        ]
    }

def upload_to_youtube(video_path, title, description):
    if not (YT_CLIENT_ID and YT_CLIENT_SECRET and YT_REFRESH_TOKEN):
        raise RuntimeError("YouTube API credentials (YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN) are missing in GitHub Secrets!")

    creds = Credentials(
        token=None,
        refresh_token=YT_REFRESH_TOKEN,
        token_uri="https" + "://[oauth2.googleapis.com/token](https://oauth2.googleapis.com/token)",
        client_id=YT_CLIENT_ID,
        client_secret=YT_CLIENT_SECRET,
        scopes=["https" + "://[www.googleapis.com/auth/youtube.upload](https://www.googleapis.com/auth/youtube.upload)"]
    )

    youtube = build("youtube", "v3", credentials=creds)

    body = {
        "snippet": {
            "title": title[:95],
            "description": description,
            "tags": ["psychology", "human psychology", "mindset", "shorts", "decode yourself"],
            "categoryId": "27"
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False
        }
    }

    media = MediaFileUpload(video_path, chunksize=1024*1024, resumable=True, mimetype="video/mp4")
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    print("Uploading directly to YouTube...")
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"Upload progress: {int(status.progress() * 100)}%")

    video_id = response.get("id")
    print("SUCCESS! Video published on YouTube: https" + f"://[www.youtube.com/shorts/](https://www.youtube.com/shorts/){video_id}")
    return video_id

def main():
    topic = "Why people overthink at night"
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if event_path and os.path.exists(event_path):
        try:
            with open(event_path, "r", encoding="utf-8") as f:
                event_data = json.load(f)
            payload = event_data.get("client_payload", {})
            topic = payload.get("topic", payload.get("ai_data", "Why people overthink at night"))
        except Exception as e:
            print(f"Error parsing event payload: {e}")

    print(f"Generating 60-second content for topic: {topic}")
    parsed = call_gemini_api(topic)

    script = parsed.get("script", "").strip()
    prompts = parsed.get("image_prompts", ["glowing human brain in dark room"])
    title = parsed.get("title", "Psychology Fact #shorts")
    description = parsed.get("description", "Decode your mind. #psychology #shorts")

    if isinstance(prompts, str):
        prompts = [p.strip() for p in prompts.split("|") if p.strip()]
    if not prompts:
        prompts = ["glowing human brain in dark room"]

    print("1. Generating Voiceover & Subtitles...")
    try:
        has_subs = asyncio.run(generate_audio_and_subs(script, "voice.mp3", "subs.srt"))
    except Exception as e:
        print(f"TTS Error encountered: {e}. Using fallback script.")
        fallback_script = "Your brain processes thousands of thoughts every single day, shaping your reality without you even realizing it."
        has_subs = asyncio.run(generate_audio_and_subs(fallback_script, "voice.mp3", "subs.srt"))
        script = fallback_script

    if not os.path.exists("voice.mp3") or os.path.getsize("voice.mp3") < 1000:
        raise RuntimeError("Voice audio file was not generated correctly.")

    audio = AudioFileClip("voice.mp3")
    total_duration = audio.duration
    if not has_subs:
        build_fallback_srt(script, total_duration, "subs.srt")

    clip_duration = total_duration / max(len(prompts), 1)

    print(f"2. Downloading AI Images for {total_duration:.1f}s duration...")
    clips = []
    for idx, prompt in enumerate(prompts):
        img_file = f"img_{idx}.jpg"
        if download_image(prompt, img_file):
            clips.append(create_zoom_clip(img_file, clip_duration))

    if not clips:
        raise RuntimeError("No images could be downloaded.")

    print("3. Assembling Video with Cinematic Zoom...")
    video = concatenate_videoclips(clips, method="compose").set_audio(audio)
    video.write_videofile("raw_video.mp4", fps=24, codec="libx264", audio_codec="aac")

    print("4. Burning Professional Captions & Formatting via FFmpeg...")
    subtitle_style = "Alignment=10,FontName=Arial,FontSize=20,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=1,Bold=1"
    vf_filter = f"subtitles=subs.srt:force_style='{subtitle_style}',scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2"
    
    os.system(f'ffmpeg -y -i raw_video.mp4 -vf "{vf_filter}" -c:v libx264 -preset medium -pix_fmt yuv420p -c:a aac -b:a 192k -movflags +faststart final_video.mp4')

    if not os.path.exists("final_video.mp4") or os.path.getsize("final_video.mp4") < 50000:
        raise RuntimeError("Final video generation failed or file is too small.")

    print(f"Video successfully created! File size: {os.path.getsize('final_video.mp4') / (1024*1024):.2f} MB")

    print("5. Uploading Directly to YouTube...")
    upload_to_youtube("final_video.mp4", title, description)
    print("All Done!")

if __name__ == "__main__":
    main()
