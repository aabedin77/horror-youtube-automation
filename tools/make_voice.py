import os, json, glob, sys, time, asyncio
import edge_tts
from mutagen.mp3 import MP3

voice = (os.environ.get("VOICE") or "hi-IN-SwaraNeural").strip()
rate = (os.environ.get("RATE") or "-5%").strip()
force = (os.environ.get("FORCE") or "no").strip().lower() == "yes"
want = os.environ.get("SHOTS_DATE", "").strip()
try:
    limit = int(os.environ.get("LIMIT_SHOTS") or 0)
except ValueError:
    limit = 0
MAX_SECONDS = 5.0
GAP = 0.2

files = sorted(glob.glob("scenes/shots-*.json"))
if not files:
    sys.exit("ERROR: no scenes/shots-DATE.json found. Run Make Shots first.")
if want:
    src = "scenes/shots-" + want + ".json"
    if not os.path.exists(src):
        have = ", ".join(os.path.basename(f)[6:-5] for f in files)
        sys.exit("ERROR: no shots file for " + want + ". Available: " + have)
else:
    src = files[-1]
data = json.load(open(src, encoding="utf-8"))
date = data["date"]
shots = data["shots"]
if limit > 0:
    shots = shots[:limit]
print("Using:", src, "with", len(shots), "shots; voice", voice, "rate", rate)

outdir = "audio/shots-" + date
os.makedirs(outdir, exist_ok=True)

async def speak(text, path):
    com = edge_tts.Communicate(text, voice=voice, rate=rate)
    await com.save(path)

def make_one(text, path):
    last = ""
    for attempt in range(1, 4):
        try:
            asyncio.run(speak(text, path))
            if os.path.exists(path) and os.path.getsize(path) > 2000:
                return True, ""
            last = "audio file empty or too small"
        except Exception as e:
            last = repr(e)[:200]
        print("  retry", attempt, last)
        time.sleep(3 * attempt)
    return False, last

created, reused, failed, errors = 0, 0, [], []
rows = []
t_start = time.time()

for sh in shots:
    sid = sh["id"]
    path = f"{outdir}/shot-{sid:03d}.mp3"
    if force and os.path.exists(path):
        os.remove(path)
    ok = os.path.exists(path) and os.path.getsize(path) > 2000
    if ok:
        reused += 1
    else:
        ok, why = make_one(sh["text"], path)
        if ok:
            created += 1
            if created % 20 == 0:
                print(f"{created} voices created...")
        else:
            failed.append(sid)
            errors.append(f"shot {sid}: {why}")
            continue
    try:
        secs = MP3(path).info.length
    except Exception as e:
        failed.append(sid)
        errors.append(f"shot {sid}: cannot read audio length {repr(e)[:80]}")
        continue
    rows.append({"id": sid, "text": sh["text"], "audio": path,
                 "seconds": round(secs, 3)})

total = sum(r["seconds"] for r in rows)
long_ones = [r for r in rows if r["seconds"] > MAX_SECONDS]
longest = sorted(rows, key=lambda r: -r["seconds"])[:5]
video_len = total + GAP * len(rows)

os.makedirs("scenes", exist_ok=True)
out = f"scenes/timing-{date}.json"
with open(out, "w", encoding="utf-8") as f:
    json.dump({"date": date, "voice": voice, "rate": rate,
               "total_seconds": round(total, 2), "gap": GAP, "shots": rows},
              f, ensure_ascii=False, indent=2)

def mmss(x):
    return f"{int(x // 60)}m {int(x % 60)}s"

lines = [
    "## Voices result",
    f"- Voice: {voice}, rate {rate}",
    f"- Shots: {len(shots)}, voices ready: {len(rows)} (new {created}, reused {reused})",
    f"- Failed: {len(failed)} {failed}",
    f"- Total narration: {mmss(total)}",
    f"- Estimated video with {GAP}s gaps: {mmss(video_len)}",
    f"- Average per shot: {total / max(1, len(rows)):.1f}s",
    f"- Shots longer than {MAX_SECONDS:.0f}s: {len(long_ones)} "
    + str([r['id'] for r in long_ones][:15]),
    "- Longest: " + ", ".join(f"#{r['id']} {r['seconds']:.1f}s" for r in longest),
    f"- Saved: {out}",
    f"- Build time: {(time.time() - t_start) / 60:.1f} min",
]
if errors:
    lines.append("- Last errors:")
    for e in errors[-2:]:
        lines.append("  - " + e)
report = "\n".join(lines) + "\n"
print(report)
with open(os.environ.get("GITHUB_STEP_SUMMARY", "/dev/null"), "a",
          encoding="utf-8") as f:
    f.write(report)

if failed:
    sys.exit("Some voices failed: " + str(failed))
# END_OF_FILE_MARKER￼Enter
