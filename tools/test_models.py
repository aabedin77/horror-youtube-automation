import os, json, sys, time, base64, uuid
import urllib.request, urllib.error

acct = os.environ.get("CF_ACCOUNT_ID", "").strip()
token = os.environ.get("CF_API_TOKEN", "").strip()
if not acct or not token:
    sys.exit("ERROR: CF_ACCOUNT_ID or CF_API_TOKEN secret is missing")

W, H = 1024, 576
STYLE = ("hand-drawn 2D cartoon illustration, expressive faces, bold clean "
         "outlines, detailed painterly background, moody horror atmosphere, "
         "wide cinematic 16:9 composition, no text, no letters, no watermark. ")
PROMPTS = [
    STYLE + "A young Indian man with short dark hair and a grey shirt stands "
            "in front of an old abandoned village house at sunset, holding a "
            "suitcase, worried expression, long shadows.",
    STYLE + "The same young man at night in a dusty room, staring at a large "
            "old mirror, a faint shadow of a small girl visible behind him "
            "in the mirror, candle light.",
    STYLE + "A dark narrow wooden staircase going down into a basement, a "
            "frightened young man walking down slowly, thin cold mist on the "
            "steps, teal and green light.",
]
MODELS = [
    ("klein4b", "@cf/black-forest-labs/flux-2-klein-4b", "multipart"),
    ("klein9b", "@cf/black-forest-labs/flux-2-klein-9b", "multipart"),
    ("sdxlLightning", "@cf/bytedance/stable-diffusion-xl-lightning", "json"),
]

def url_for(model):
    return ("https://api.cloudflare.com/client/v4/accounts/" + acct
            + "/ai/run/" + model)

def multipart(fields):
    boundary = "----form" + uuid.uuid4().hex
    parts = []
    for k, v in fields.items():
        parts.append(("--" + boundary + "\r\n"
                      'Content-Disposition: form-data; name="' + k + '"\r\n\r\n'
                      + str(v) + "\r\n").encode())
    parts.append(("--" + boundary + "--\r\n").encode())
    return b"".join(parts), "multipart/form-data; boundary=" + boundary

def extract_image(raw):
    if raw[:2] == b"\xff\xd8" or raw[:4] == b"\x89PNG":
        return raw
    d = json.loads(raw)
    if isinstance(d.get("result"), dict) and d["result"].get("image"):
        return base64.b64decode(d["result"]["image"])
    if d.get("image"):
        return base64.b64decode(d["image"])
    raise ValueError("no image in response: " + json.dumps(d)[:200])

def call(model, kind, prompt, extra=None):
    fields = {"prompt": prompt, "width": W, "height": H}
    if extra:
        fields.update(extra)
    if kind == "multipart":
        body, ctype = multipart(fields)
    else:
        body, ctype = json.dumps(fields).encode(), "application/json"
    req = urllib.request.Request(
        url_for(model), data=body,
        headers={"Content-Type": ctype, "Authorization": "Bearer " + token,
                 "User-Agent": "horror-automation/1.0"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return extract_image(r.read())

def image_size(raw):
    try:
        import struct
        if raw[:4] == b"\x89PNG":
            return struct.unpack(">II", raw[16:24])
        i = 2
        while i < len(raw):
            if raw[i] != 0xFF:
                i += 1
                continue
            m = raw[i + 1]
            if 0xC0 <= m <= 0xCF and m not in (0xC4, 0xC8, 0xCC):
                h, w = struct.unpack(">HH", raw[i + 5:i + 9])
                return w, h
            i += 2 + struct.unpack(">H", raw[i + 2:i + 4])[0]
    except Exception:
        pass
    return None

os.makedirs("test", exist_ok=True)
lines = ["## Model comparison (16:9 request " + f"{W}x{H})"]
quota = False
for name, model, kind in MODELS:
    if quota:
        break
    for n, p in enumerate(PROMPTS, 1):
        t0 = time.time()
        raw, note = None, ""
        for extra in ([{"num_steps": 4}, None] if name == "sdxlLightning" else [None]):
            try:
                raw = call(model, kind, p, extra)
                break
            except urllib.error.HTTPError as e:
                body = e.read().decode()[:220]
                note = f"HTTP {e.code} {body}"
                if e.code == 429 or "neuron" in body.lower() or "4006" in body:
                    quota = True
                    break
            except Exception as e:
                note = repr(e)[:220]
        if raw:
            ext = "png" if raw[:4] == b"\x89PNG" else "jpg"
            path = f"test/model-{name}-{n}.{ext}"
            open(path, "wb").write(raw)
            lines.append(f"- {name} #{n}: SAVED {path} ({len(raw)//1024} KB, "
                         f"{time.time()-t0:.0f}s, size {image_size(raw)})")
        else:
            lines.append(f"- {name} #{n}: FAILED {note}")
        if quota:
            lines.append("- Daily quota used up, stopping.")
            break
        time.sleep(1)

report = "\n".join(lines) + "\n"
print(report)
with open(os.environ.get("GITHUB_STEP_SUMMARY", "/dev/null"), "a",
          encoding="utf-8") as f:
    f.write(report)
# END_OF_FILE_MARKER￼Enter
