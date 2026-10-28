import os, json, glob, sys, time, base64
import urllib.request, urllib.error

acct = os.environ.get("CF_ACCOUNT_ID", "").strip()
token = os.environ.get("CF_API_TOKEN", "").strip()
if not acct or not token:
    sys.exit("ERROR: CF_ACCOUNT_ID or CF_API_TOKEN secret is missing")

try:
    max_images = int(os.environ.get("MAX_IMAGES") or 40)
except ValueError:
    max_images = 40
try:
    steps = max(1, min(8, int(os.environ.get("STEPS") or 4)))
except ValueError:
    steps = 4
want = os.environ.get("SHOTS_DATE", "").strip()
TIME_LIMIT = 35 * 60

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
print("Using:", src, "with", len(shots), "shots")

outdir = "images/shots-" + date
os.makedirs(outdir, exist_ok=True)

url = ("https://api.cloudflare.com/client/v4/accounts/" + acct
       + "/ai/run/@cf/black-forest-labs/flux-1-schnell")

FALLBACK_STYLE = ("2D animated graphic-novel style, thick clean ink outlines, "
                  "flat cel shading, muted desaturated green and teal color "
                  "palette, moody cinematic lighting, highly detailed "
                  "background, no text, no letters, no watermark. ")

def existing(sid):
    for ext in ("jpg", "png"):
        p = f"{outdir}/shot-{sid:03d}.{ext}"
        if os.path.exists(p) and os.path.getsize(p) > 20000:
            return p
    return None

def generate(prompt):
    payload = {"prompt": prompt[:1900], "steps": steps}
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + token,
                 "User-Agent": "horror-automation/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        d = json.loads(r.read())
    return base64.b64decode(d["result"]["image"])

def is_quota(code, body):
    b = body.lower()
    return (code == 429 or "neuron" in b or "daily free allocation" in b
            or "4006" in b)

created, skipped, failed, errors, times = 0, 0, [], [], []
quota_hit = False
stop_reason = ""
t_start = time.time()

for sh in shots:
    sid = sh["id"]
    if existing(sid):
        skipped += 1
        continue
    if created >= max_images:
        stop_reason = "reached the limit for this run"
        break
    if time.time() - t_start > TIME_LIMIT:
        stop_reason = "time limit for this run"
        break

    full = sh["image_prompt"]
    plain = FALLBACK_STYLE + full.split("Shot:", 1)[-1].strip()
    attempts = [full, full, plain]
    done = False
    for n, p in enumerate(attempts, 1):
        t0 = time.time()
        try:
            raw = generate(p)
            if len(raw) < 20000:
                raise ValueError("image too small")
            ext = "png" if raw[:4] == b"\x89PNG" else "jpg"
            with open(f"{outdir}/shot-{sid:03d}.{ext}", "wb") as f:
                f.write(raw)
            times.append(time.time() - t0)
            created += 1
            done = True
            print(f"shot {sid}: ok (try {n}, {times[-1]:.1f}s)")
            break
        except urllib.error.HTTPError as e:
            body = e.read().decode()[:300]
            print(f"shot {sid} try {n}: HTTP {e.code} {body[:150]}")
            errors.append(f"shot {sid}: HTTP {e.code} {body[:120]}")
            if is_quota(e.code, body):
                quota_hit = True
                break
            time.sleep(4 * n)
        except Exception as e:
            print(f"shot {sid} try {n}: {repr(e)[:150]}")
            errors.append(f"shot {sid}: {repr(e)[:120]}")
            time.sleep(4 * n)
    if quota_hit:
        stop_reason = "daily free quota is used up"
        break
    if not done:
        failed.append(sid)
    time.sleep(0.5)

have = sum(1 for sh in shots if existing(sh["id"]))
avg = (sum(times) / len(times)) if times else 0
lines = [
    "## Images result",
    f"- Shots file: {src}",
    f"- Images now in folder: {have} of {len(shots)}",
    f"- Created in this run: {created} (limit {max_images}, steps {steps})",
    f"- Already existed: {skipped}",
    f"- Failed shots: {len(failed)} {failed}",
    f"- Average time per image: {avg:.1f}s",
    f"- Daily quota used up: {'YES' if quota_hit else 'no'}",
    f"- Stopped because: {stop_reason or 'all shots done'}",
    f"- Still missing: {len(shots) - have}",
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
    sys.exit("Some shots failed: " + str(failed))
# END_OF_FILE_MARKER
