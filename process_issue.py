import json, os, re, subprocess, sys, urllib.request, io

THRESHOLD = 3  # при таком остатке и меньше появится заявка «Допечатать»
ITEMS = "items.json"
RESULT = "/tmp/result.json"
TR = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
  ["a","b","v","g","d","e","e","zh","z","i","y","k","l","m","n","o","p","r","s","t","u","f","h","c","ch","sh","sch","","y","","e","yu","ya"]))

def slug(s):
    s = "".join(TR.get(c, c) for c in s.lower())
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-") or "item"

def num(s):
    m = re.search(r"\d+", s or "")
    return int(m.group()) if m else None

def parse(body):
    parts = re.split(r"^### +(.+?)\s*$", body, flags=re.M)
    out = {}
    for i in range(1, len(parts) - 1, 2):
        v = parts[i + 1].strip()
        out[parts[i].strip()] = "" if v == "_No response_" else v
    return out

def load():
    return json.load(open(ITEMS, encoding="utf-8"))

def save(d):
    with open(ITEMS, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

def find(items, q):
    q = q.strip().lower()
    for it in items:
        if q in (it["id"].lower(), it["name"].lower()):
            return it
    return None

def get_image(text, item_id):
    m = re.search(r'\((https?://[^)\s]+)\)', text) or re.search(r'src="([^"]+)"', text)
    if not m:
        return None
    req = urllib.request.Request(m.group(1), headers={"User-Agent": "merch-bot"})
    data = urllib.request.urlopen(req, timeout=60).read()
    path = f"images/{item_id}.jpg"
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(data))
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            bg = Image.new("RGB", im.size, "white")
            bg.paste(im, mask=im.split()[-1])
            im = bg
        im = im.convert("RGB")
        im.thumbnail((900, 900))
        im.save(path, "JPEG", quality=82, optimize=True)
    except Exception:
        open(path, "wb").write(data)
    return path

def process():
    ev = json.load(open(os.environ["GITHUB_EVENT_PATH"]))["issue"]
    f = parse(ev["body"] or "")
    res = {"ok": True, "msg": "", "commit": "", "reprint": [], "clear": []}
    d = load(); items = d["items"]
    try:
        it = None
        if "Цена, ₽" in f:
            name = f["Название"].strip()
            if find(items, name):
                raise ValueError(f"Позиция «{name}» уже есть. Чтобы изменить остаток, используйте форму «Изменить остаток».")
            price, qty = num(f["Цена, ₽"]), num(f["Количество"])
            if price is None or qty is None:
                raise ValueError("Цена и количество должны быть числами.")
            base = slug(name); iid = base; n = 2
            while any(i["id"] == iid for i in items):
                iid = f"{base}-{n}"; n += 1
            img = get_image(f.get("Картинка", ""), iid)
            it = {"id": iid, "name": name, "fandom": f["Фандом"].strip(), "type": f["Вид"].strip(),
                  "price": price, "size": f.get("Размеры", ""), "material": f.get("Материал", ""),
                  "qty": qty, "image": img or ""}
            items.append(it)
            res["msg"] = f"Добавлено: {name}." + ("" if img else " Картинку найти не удалось, позиция добавлена без неё.")
            res["commit"] = f"Новая позиция: {name}"
        elif "Новое количество" in f:
            it = find(items, f["Позиция"])
            qty = num(f["Новое количество"])
            if not it: raise ValueError(f"Не нашла позицию «{f['Позиция']}». Проверьте название.")
            if qty is None: raise ValueError("Количество должно быть числом.")
            old = it["qty"]; it["qty"] = qty
            res["msg"] = f"«{it['name']}»: остаток {old} → {qty}."
            res["commit"] = f"Остаток {it['name']}: {qty}"
        elif "Позиция" in f:
            it = find(items, f["Позиция"])
            if not it: raise ValueError(f"Не нашла позицию «{f['Позиция']}». Проверьте название.")
            items.remove(it)
            if it["image"] and not any(i["image"] == it["image"] for i in items) and os.path.exists(it["image"]):
                os.remove(it["image"])
            res["msg"] = f"Удалено: {it['name']}."
            res["commit"] = f"Удалено: {it['name']}"
            res["clear"].append(it["name"])
            it = None
        else:
            sys.exit(0)
        if it:
            if it["qty"] <= THRESHOLD:
                res["reprint"].append({"name": it["name"], "qty": it["qty"]})
            else:
                res["clear"].append(it["name"])
        save(d)
    except Exception as e:
        res.update(ok=False, msg=f"Не получилось: {e}", commit="")
    json.dump(res, open(RESULT, "w", encoding="utf-8"), ensure_ascii=False)

def gh(*a):
    return subprocess.run(["gh", *a], capture_output=True, text=True).stdout

def notify():
    num_ = str(json.load(open(os.environ["GITHUB_EVENT_PATH"]))["issue"]["number"])
    r = json.load(open(RESULT, encoding="utf-8"))
    gh("issue", "comment", num_, "--body", ("✅ " if r["ok"] else "⚠️ ") + r["msg"])
    open_ = json.loads(gh("issue", "list", "--state", "open", "--limit", "200", "--json", "number,title") or "[]")
    for x in r["reprint"]:
        t = f"Допечатать: {x['name']}"
        if not any(i["title"] == t for i in open_):
            gh("issue", "create", "--title", t, "--body", f"Осталось {x['qty']} шт. Пора допечатать.")
    for name in r["clear"]:
        for i in open_:
            if i["title"] == f"Допечатать: {name}":
                gh("issue", "close", str(i["number"]), "--comment", "Остаток пополнен или позиция убрана.")
    gh("issue", "close", num_, "--reason", "completed" if r["ok"] else "not planned")

if __name__ == "__main__":
    process() if sys.argv[1] == "process" else notify()
