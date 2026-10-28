import os, re, json, glob, sys, time, math, hashlib
import urllib.request, urllib.error

MAX_WORDS = 10
BATCH = 30
WORDS_PER_SEC = 2.1
key = os.environ.get("GEMINI_API_KEY", "")
dry = os.environ.get("DRY_RUN") == "1"

want = os.environ.get("SCRIPT_DATE", "").strip()
files = sorted(glob.glob("scripts/horror-*.md"))
if not files:
    sys.exit("ERROR: no script found in scripts/")
if want:
    src = "scripts/horror-" + want + ".md"
    if not os.path.exists(src):
        have = ", ".join(os.path.basename(f)[len("horror-"):-3] for f in files)
        sys.exit("ERROR: no script for date " + want + ". Available dates: " + have)
else:
    src = files[-1]
date = os.path.basename(src)[len("horror-"):-3]
print("Using script:", src)
raw = open(src, encoding="utf-8").read().strip()
rows = raw.split("\n")
title = rows[0].lstrip("# ").strip()
story = "\n".join(rows[1:]).strip()

# ---------- split into short shots ----------
def pieces(sentence):
    words = sentence.split()
    if len(words) <= MAX_WORDS:
        return [sentence]
    clauses = [c.strip() for c in
               re.split(r"(?<=[,;:])\s+|\s+[\u2014\u2013]\s+", sentence)
               if c.strip()]
    out, cur = [], []
    def flush():
        if cur:
            out.append(" ".join(cur))
            cur.clear()
    for c in clauses:
        cw = c.split()
        if len(cw) > MAX_WORDS:
            flush()
            n = math.ceil(len(cw) / MAX_WORDS)
            size = math.ceil(len(cw) / n)
            for i in range(0, len(cw), size):
                out.append(" ".join(cw[i:i + size]))
            continue
        if len(cur) + len(cw) > MAX_WORDS:
            flush()
        cur.extend(cw)
    flush()
    return out

sentences = [s.strip() for s in
             re.split(r"(?<=[\u0964!?\.])\s+", story.replace("\n", " "))
             if s.strip()]
raw_pieces = []
for s in sentences:
    raw_pieces.extend(pieces(s))

shots_text = []
for p in raw_pieces:
    if shots_text and len(shots_text[-1].split()) < 5 \
            and len(shots_text[-1].split()) + len(p.split()) <= MAX_WORDS:
        shots_text[-1] += " " + p
    else:
        shots_text.append(p)

counts = [len(t.split()) for t in shots_text]
total_words = sum(counts)
print(f"Shots: {len(shots_text)}, words: {total_words}, max words in a shot: {max(counts)}")

# ---------- Gemini helpers ----------
prompts = {}
SAFETY = [
    {"category": c, "threshold": "BLOCK_ONLY_HIGH"}
    for c in ("HARM_CATEGORY_HARASSMENT",
              "HARM_CATEGORY_HATE_SPEECH",
              "HARM_CATEGORY_SEXUALLY_EXPLICIT",
              "HARM_CATEGORY_DANGEROUS_CONTENT")
]

def fail(msg):
    print("ERROR:", msg)
    with open(os.environ.get("GITHUB_STEP_SUMMARY", "/dev/null"), "a",
              encoding="utf-8") as f:
        f.write("## Shots FAILED\n- Script used: " + src + "\n"
                "- Prompts saved so far: " + str(len(prompts)) + " of "
                + str(len(shots_text)) + " (run again to continue)\n"
                "- Reason: " + msg[:900] + "\n")
    sys.exit(1)

def call_gemini(prompt, model):
    url = ("https://generativelanguage.googleapis.com/v1beta/"
           "models/" + model + ":generateContent")
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "safetySettings": SAFETY,
        "generationConfig": {
            "maxOutputTokens": 16000,
            "temperature": 0.7,
            "responseMimeType": "application/json",
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "x-goog-api-key": key})
    with urllib.request.urlopen(req, timeout=180) as r:
        data = json.loads(r.read())
    if not data.get("candidates"):
        raise RuntimeError(
            "Gemini returned no candidates. promptFeedback="
            + json.dumps(data.get("promptFeedback", {}))[:300]
            + " raw=" + json.dumps(data)[:300])
    cand = data["candidates"][0]
    parts = cand.get("content", {}).get("parts", [])
    text = "".join(p["text"] for p in parts if "text" in p).strip()
    if not text:
        raise RuntimeError(
            "Gemini returned empty text. finishReason="
            + str(cand.get("finishReason"))
            + " safetyRatings="
            + json.dumps(cand.get("safetyRatings", []))[:300])
    text = re.sub(r"^```(?:json)?|```$", "", text).strip()
    try:
        return json.loads(text)
    except Exception:
        raise RuntimeError(
            "Gemini text was not valid JSON. finishReason="
            + str(cand.get("finishReason")) + " start=" + text[:200])

PLAN = [("gemini-2.5-flash", 15), ("gemini-2.5-flash", 30),
        ("gemini-2.5-flash-lite", 45), ("gemini-2.5-flash-lite", 60),
        ("gemini-2.5-flash", 90), ("gemini-2.5-flash", 120)]

def with_retry(prompt, check):
    last = ""
    for attempt, (model, wait) in enumerate(PLAN, 1):
        try:
            result = call_gemini(prompt, model)
            ok, why = check(result)
            if ok:
                if model != PLAN[0][0]:
                    print("(used backup model " + model + ")")
                return result
            last = why
            print(f"Attempt {attempt} [{model}]: {why}")
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code} {e.read().decode()[:200]}"
            print(f"Attempt {attempt} [{model}]:", last)
        except Exception as e:
            last = repr(e)[:300]
            print(f"Attempt {attempt} [{model}]:", last)
        if attempt < len(PLAN):
            time.sleep(wait)
    fail("Gemini failed after " + str(len(PLAN)) + " tries: " + last)

# ---------- 1) character + world bible ----------
BIBLE_PROMPT = (
    "Read this Hindi horror story and return ONLY valid JSON: "
    '{"character": "...", "world": "..."}\n'
    "character: English, 2 sentences. Age, build, face, hair, "
    "clothes and one distinctive feature of the MAIN character. "
    "Be specific so the same person can be drawn again and again.\n"
    "world: English, 2 sentences. Era, place and overall look "
    "of the main setting (Indian context).\n\nStory:\n" + story
)

story_hash = hashlib.md5(story.encode("utf-8")).hexdigest()[:12]
partial_path = f"scenes/partial-{date}.json"
bible = None
if os.path.exists(partial_path) and not dry:
    try:
        pj = json.load(open(partial_path, encoding="utf-8"))
        if pj.get("hash") == story_hash and pj.get("n") == len(shots_text):
            bible = pj["bible"]
            prompts.update({int(k): v for k, v in pj["prompts"].items()})
            print(f"Resuming: {len(prompts)} prompts already done")
    except Exception as e:
        print("Could not read saved progress:", repr(e)[:200])

if bible is not None:
    pass
elif dry:
    bible = {"character": "DRY character", "world": "DRY world"}
else:
    bible = with_retry(
        BIBLE_PROMPT,
        lambda r: (isinstance(r, dict) and r.get("character")
                   and r.get("world"), "bible incomplete"))
character = bible["character"].strip()
world = bible["world"].strip()

# ---------- 2) detailed image prompts in batches ----------
STYLE = ("2D animated graphic-novel style, thick clean ink "
         "outlines, flat cel shading, muted desaturated green "
         "and teal color palette, moody cinematic lighting, "
         "highly detailed background, whole subject centered "
         "in frame, no text, no letters, no signs, no watermark. ")

def save_partial():
    os.makedirs("scenes", exist_ok=True)
    with open(partial_path, "w", encoding="utf-8") as f:
        json.dump({"hash": story_hash, "n": len(shots_text), "bible": bible,
                   "prompts": prompts}, f, ensure_ascii=False)

for start in range(0, len(shots_text), BATCH):
    chunk = list(range(start, min(start + BATCH, len(shots_text))))
    if all((i + 1) in prompts for i in chunk):
        continue
    lines = "\n".join(f"{i + 1}: {shots_text[i]}" for i in chunk)
    ids = [i + 1 for i in chunk]
    PROMPT = (
        "You are the storyboard artist for a Hindi horror video.\n"
        "Full story (for context):\n" + story + "\n\n"
        "Main character: " + character + "\n"
        "World: " + world + "\n\n"
        "Below are numbered narration lines (each about 4 seconds). "
        "For EACH line write ONE detailed English image prompt "
        "(50 to 70 words) for the picture shown while that line "
        "is spoken: setting, objects, lighting, camera angle, "
        "mood, and what the character is doing.\n"
        "Rules:\n"
        "- Vary camera angles: wide, medium, low angle, over the "
        "shoulder, silhouette, detail of an object.\n"
        "- has_character is true only if the main character is "
        "visible in the picture.\n"
        "- Do NOT include any text, signs, letters or writing.\n"
        "- Scary and atmospheric but NOT gory. No real people "
        "or brands.\n"
        "- Keep every prompt consistent with the world above.\n"
        "Return ONLY valid JSON: "
        '{"shots": [{"id": 1, "prompt": "...", "has_character": true}]}\n'
        "Return exactly these ids: " + str(ids) + "\n\n"
        "Narration lines:\n" + lines
    )

    def check(r, ids=ids):
        if not isinstance(r, dict) or "shots" not in r:
            return False, "no shots key"
        got = [s.get("id") for s in r["shots"]]
        if got != ids:
            return False, f"ids mismatch, got {len(got)} need {len(ids)}"
        if any(not str(s.get("prompt", "")).strip() for s in r["shots"]):
            return False, "empty prompt"
        return True, ""

    if dry:
        res = {"shots": [{"id": i, "prompt": "dry prompt " + str(i),
                          "has_character": i % 2 == 0} for i in ids]}
    else:
        res = with_retry(PROMPT, check)
        time.sleep(8)
    for s in res["shots"]:
        prompts[s["id"]] = s
    if not dry:
        save_partial()
    print(f"Prompts done: {len(prompts)} / {len(shots_text)}")

# ---------- 3) save ----------
shots = []
for i, t in enumerate(shots_text):
    p = prompts[i + 1]
    ip = STYLE
    if p.get("has_character"):
        ip += "Main character: " + character + " "
    ip += "Shot: " + str(p["prompt"]).strip()
    shots.append({
        "id": i + 1,
        "text": t,
        "words": len(t.split()),
        "has_character": bool(p.get("has_character")),
        "image_prompt": ip[:1900],
    })

os.makedirs("scenes", exist_ok=True)
out = f"scenes/shots-{date}.json"
with open(out, "w", encoding="utf-8") as f:
    json.dump({"title": title, "date": date, "character": character,
               "world": world, "shots": shots},
              f, ensure_ascii=False, indent=2)

if os.path.exists(partial_path):
    os.remove(partial_path)

est = total_words / WORDS_PER_SEC
over = sum(1 for c in counts if c > MAX_WORDS)
with_char = sum(1 for s in shots if s["has_character"])
report = (
    "## Shots result\n"
    f"- Script: {src}\n"
    f"- Title: {title}\n"
    f"- Total words: {total_words}\n"
    f"- Shots (= images needed): {len(shots)}\n"
    f"- Words per shot: avg {total_words // len(shots)}, max {max(counts)}\n"
    f"- Estimated narration: {int(est // 60)}m {int(est % 60)}s\n"
    f"- Shots with the main character: {with_char}\n"
    f"- Saved: {out}\n"
    f"- Shot 1 text: {shots[0]['text']}\n"
    f"- Shot 1 prompt: {shots[0]['image_prompt'][:350]}\n"
)
print(report)
with open(os.environ.get("GITHUB_STEP_SUMMARY", "/dev/null"), "a",
          encoding="utf-8") as f:
    f.write(report)
# END_OF_FILE_MARKER
