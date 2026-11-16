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
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

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

def call_gemini_api(topic):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
    
    prompt = f"""You are a viral YouTube Shorts & Facebook Reels scriptwriter for the channel "Decode Yourself".
Create 1 unique, mind-blowing human psychology fact short video script (35-45 seconds spoken) based on this topic: "{topic}".

STRICT RULES:
1. Return ONLY valid raw JSON (no markdown, no ```json tags, no extra text).
2. Use this exact JSON structure:
{{
  "topic": "Short 4-6 word topic name",
  "title": "Catchy viral title under 60 chars #shorts",
  "description": "Engaging 2-line caption with hashtags #psychology #mindset #shorts",
  "script": "Full spoken voiceover script in simple, punchy English. Start with a strong hook.",
  "image_prompts": [
    "Vertical 9:16 dark moody cinematic illustration of scene 1, no text",
    "Vertical 9:16 dark moody cinematic illustration of scene 2, no text",
    "Vertical 9:16 dark moody cinematic illustration of scene 3, no text"
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
    
    # Fallback if API fails
    return {
        "topic": topic,
        "title": f"{topic} #shorts",
        "description": f"Fascinating psychology fact about {topic}. #psychology #shorts",
        "script": f"Here is an incredible psychological fact about {topic}. Your brain constantly processes information in ways you never realized, shaping your daily decisions.",
        "image_prompts": [
            "Vertical 9:16 dark moody cinematic illustration of human brain glowing",
            "Vertical 9:16 dark moody cinematic illustration of person thinking deeply"
        ]
    }

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

    print(f"Generating content for topic: {topic}")
    parsed = call_gemini_api(topic)

    script = parsed.get("script", "").strip()
    prompts = parsed.get("image_prompts", ["glowing human brain in dark room"])
    title = parsed.get("title", "Psychology Fact #shorts")
    description = parsed.get("description", "Decode your mind. #psychology #shorts")
    webhook_url = MAKE_WEBHOOK_URL

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

    print("4. Burning Captions & Formatting for Facebook/YouTube via FFmpeg...")
    subtitle_style = "Alignment=10,FontName=Arial,FontSize=20,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=1,Bold=1"
    vf_filter = f"subtitles=subs.srt:force_style='{subtitle_style}',scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2"
    
    os.system(f'ffmpeg -y -i raw_video.mp4 -vf "{vf_filter}" -c:v libx264 -preset medium -pix_fmt yuv420p -c:a aac -b:a 192k -movflags +faststart final_video.mp4')

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
