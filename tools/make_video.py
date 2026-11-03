hereimport os, json, glob, sys, time, subprocess, wave, shutil

FONT = (os.environ.get("FONT_NAME") or "Noto Sans Devanagari").strip()
try:
    LIMIT = int(os.environ.get("LIMIT_SHOTS") or 0)
except ValueError:
    LIMIT = 0
want = os.environ.get("SHOTS_DATE", "").strip()
OUTRO_TEXT = os.environ.get("OUTRO_TEXT") or "वीडियो पसंद आया तो लाइक और सब्सक्राइब करें"
INTRO, OUTRO, FPS, MAX_SECONDS = 3.0, 4.0, 25, 5.0
t_start = time.time()

def run(cmd, what, cwd=None):
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if r.returncode != 0:
        print(r.stderr[-1500:])
        sys.exit("ERROR: " + what + " failed")
    return r

def probe(path):
    out = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries",
                                   "format=duration", "-of",
                                   "default=nw=1:nk=1", path])
    return float(out.decode().strip())

tfiles = sorted(glob.glob("scenes/timing-*.json"))
if not tfiles:
    sys.exit("ERROR: no scenes/timing-DATE.json. Run Make Voices first.")
if want:
    tpath = "scenes/timing-" + want + ".json"
    if not os.path.exists(tpath):
        sys.exit("ERROR: no timing file for " + want)
else:
    tpath = tfiles[-1]
timing = json.load(open(tpath, encoding="utf-8"))
date = timing["date"]
GAP = float(timing.get("gap", 0.2))
rows = timing["shots"]
if LIMIT > 0:
    rows = rows[:LIMIT]
title = date
spath = f"scenes/shots-{date}.json"
if os.path.exists(spath):
    title = json.load(open(spath, encoding="utf-8")).get("title") or date
print("Using:", tpath, "|", len(rows), "shots | title:", title)

imgdir = f"images/16x9-{date}"
def find_img(sid):
    for ext in ("jpg", "png"):
        p = f"{imgdir}/shot-{sid:03d}.{ext}"
        if os.path.exists(p) and os.path.getsize(p) > 20000:
            return os.path.abspath(p)
    return None

imgs = [find_img(r["id"]) for r in rows]
missing = [r["id"] for r, p in zip(rows, imgs) if not p]
if missing:
    sys.exit(f"ERROR: {len(missing)} images missing, first: {missing[:20]}. "
             "Run Make Scene Images first.")
no_audio = [r["id"] for r in rows if not os.path.exists(r["audio"])]
if no_audio:
    sys.exit(f"ERROR: audio missing for shots {no_audio[:20]}. Run Make Voices first.")

work = f"work/video-{date}"
shutil.rmtree(work, ignore_errors=True)
os.makedirs(work + "/a")
os.makedirs("out", exist_ok=True)
W = os.path.abspath(work)

# ---- 1) exact narration track: every shot padded with a short gap ----
durs = []
for r in rows:
    wav = f"{W}/a/{r['id']:03d}.wav"
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", r["audio"], "-af",
         f"apad=pad_dur={GAP}", "-ar", "48000", "-ac", "1",
         "-c:a", "pcm_s16le", wav], f"audio convert shot {r['id']}")
    with wave.open(wav) as w:
        durs.append(w.getnframes() / w.getframerate())
for name, secs in (("intro", INTRO), ("outro", OUTRO)):
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
         "anullsrc=r=48000:cl=mono", "-t", str(secs), "-c:a", "pcm_s16le",
         f"{W}/a/{name}.wav"], "silence " + name)
order = [f"{W}/a/intro.wav"] + [f"{W}/a/{r['id']:03d}.wav" for r in rows] \
        + [f"{W}/a/outro.wav"]
with open(f"{W}/audio.txt", "w") as f:
    for p in order:
        f.write("file '" + p + "'\n")
run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
     "-i", f"{W}/audio.txt", "-c", "copy", f"{W}/narration.wav"], "audio concat")
total = INTRO + sum(durs) + OUTRO

# ---- 2) subtitles (ASS: Hindi shaped by libass) + title and outro cards ----
def ts(x):
    cs = int(round(x * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"

def esc(t):
    return t.replace("{", "(").replace("}", ")").replace("\n", "\\N")

ass = [
    "[Script Info]", "ScriptType: v4.00+", "PlayResX: 1280", "PlayResY: 720",
    "WrapStyle: 0", "ScaledBorderAndShadow: yes", "",
    "[V4+ Styles]",
    "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,"
    "BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,"
    "BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding",
    f"Style: Sub,{FONT},44,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,0,0,0,0,"
    "100,100,0,0,1,3,1,2,90,90,34,1",
    f"Style: Card,{FONT},64,&H00FFFFFF,&H00FFFFFF,&H00000000,&HB4000000,1,0,0,0,"
    "100,100,0,0,3,16,0,5,120,120,40,1",
    "", "[Events]",
    "Format: Layer,Start,End,Name,Style,MarginL,MarginR,MarginV,Effect,Text",
    f"Dialogue: 0,{ts(0.2)},{ts(INTRO - 0.2)},,Card,0,0,0,,{{\\fad(400,400)}}{esc(title)}",
]
t = INTRO
for r, d in zip(rows, durs):
    end = min(t + float(r["seconds"]) + 0.05, t + d)
    ass.append(f"Dialogue: 0,{ts(t)},{ts(end)},,Sub,0,0,0,,{esc(r['text'])}")
    t += d
ass.append(f"Dialogue: 0,{ts(t + 0.3)},{ts(t + OUTRO - 0.3)},,Card,0,0,0,,"
           f"{{\\fad(400,400)}}{esc(OUTRO_TEXT)}")
with open(f"{W}/subs.ass", "w", encoding="utf-8") as f:
    f.write("\n".join(ass) + "\n")

# ---- 3) picture track: still images, hard cuts, exactly as long as the audio ----
with open(f"{W}/images.txt", "w") as f:
    f.write(f"file '{imgs[0]}'\nduration {INTRO:.3f}\n")
    for p, d in zip(imgs, durs):
        f.write(f"file '{p}'\nduration {d:.3f}\n")
    f.write(f"file '{imgs[-1]}'\nduration {OUTRO:.3f}\n")
    f.write(f"file '{imgs[-1]}'\n")
vf = (f"fps={FPS},scale=1280:720:flags=lanczos:force_original_aspect_ratio="
      "decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2:color=black,"
      "subtitles=subs.ass,format=yuv420p")
run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
     "-i", "images.txt", "-vf", vf, "-c:v", "libx264", "-preset", "veryfast",
     "-crf", "22", "-pix_fmt", "yuv420p", "video.mp4"], "picture render", cwd=W)

# ---- 4) quiet generated dark ambience + loudness mastering ----
T = f"{total:.2f}"
fc = (f"sine=f=55:sample_rate=48000:duration={T}[s1];"
      f"sine=f=82.4:sample_rate=48000:duration={T},tremolo=f=0.12:d=0.7[s2];"
      f"anoisesrc=color=brown:amplitude=0.5:sample_rate=48000:duration={T},"
      "lowpass=f=240[n];"
      "[s1][s2][n]amix=inputs=3:normalize=0,volume=0.55,"
      f"afade=t=in:d=4,afade=t=out:st={total - 4:.2f}:d=4[m];"
      "[1:a][m]amix=inputs=2:duration=first:normalize=0,"
      "loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000,aformat=channel_layouts=stereo[a]")
out = f"out/video-{date}.mp4"
run(["ffmpeg", "-y", "-loglevel", "error", "-i", f"{W}/video.mp4",
     "-i", f"{W}/narration.wav", "-filter_complex", fc,
     "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac",
     "-b:a", "192k", "-movflags", "+faststart", "-shortest", out],
    "final mix")

# ---- 5) report ----
vdur = probe(out)
size_mb = os.path.getsize(out) / 1024 / 1024
lufs = ""
r = subprocess.run(["ffmpeg", "-hide_banner", "-i", out, "-af", "ebur128=peak=true",
                    "-vn", "-f", "null", "-"], capture_output=True, text=True)
for line in r.stderr.splitlines()[-12:]:
    if line.strip().startswith("I:"):
        lufs = line.strip()
previews = []
for k, tt in enumerate((INTRO / 2, INTRO + 2.0, vdur - 2.0), 1):
    pth = f"out/preview-{date}-{k}.jpg"
    rr = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss",
                         f"{max(0.0, min(tt, vdur - 0.5)):.2f}", "-i", out,
                         "-frames:v", "1", "-q:v", "3", pth])
    if rr.returncode == 0 and os.path.exists(pth):
        previews.append(pth)
long_ones = [x["id"] for x, d in zip(rows, durs) if d > MAX_SECONDS]
shutil.rmtree(work, ignore_errors=True)
def mmss(x):
    return f"{int(x // 60)}m {int(x % 60)}s"
report = (
    "## Video result\n"
    f"- Title: {title}\n"
    f"- Shots used: {len(rows)}" + (f" (limit {LIMIT})" if LIMIT else " (all)") + "\n"
    f"- Video length: {mmss(vdur)} (intro {INTRO:.0f}s + outro {OUTRO:.0f}s included)\n"
    f"- File size: {size_mb:.1f} MB\n"
    f"- Loudness: {lufs or 'not measured'}\n"
    f"- Shots longer than {MAX_SECONDS:.0f}s on screen: {len(long_ones)} {long_ones[:10]}\n"
    f"- Subtitle font: {FONT}\n"
    f"- Preview frames saved: {len(previews)} (check the Hindi text in them)\n"
    f"- Build time: {(time.time() - t_start) / 60:.1f} min\n"
    f"- Output: {out}\n"
)
print(report)
with open(os.environ.get("GITHUB_STEP_SUMMARY", "/dev/null"), "a",
          encoding="utf-8") as f:
    f.write(report)
# END_OF_FILE_MARKER
