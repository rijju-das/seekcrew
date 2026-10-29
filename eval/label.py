#!/usr/bin/env python3
"""A local page for labelling the evaluation sample (127.0.0.1 only, standard library).

    python3 eval/label.py            # then open http://127.0.0.1:8780

Shows each advert's full text with buttons for relevant (yes/no) and fit (1-5) and a notes box,
and saves every change straight to eval/data/labels.csv (a backup of the file as it was is kept
in eval/data/labels.backup.csv). It shows no system's opinion, so your labels stay independent.
Keys: Y / N for relevant, 1-5 for fit, J / K or arrow keys for next / previous.
"""
import argparse
import csv
import json
import shutil
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
FIELDS = ["id", "company", "title", "location", "url", "relevant", "fit", "notes"]
LOCK = threading.Lock()


def load_items(ads_path, labels_path):
    ads = {}
    for line in Path(ads_path).read_text().splitlines():
        if line.strip():
            a = json.loads(line)
            ads[a["id"]] = a
    items = []
    with open(labels_path, newline="") as f:
        for row in csv.DictReader(f):
            a = ads.get(row["id"], {})
            items.append({"id": row["id"], "company": row["company"], "title": row["title"],
                          "location": row["location"], "url": row["url"], "source": a.get("source", ""),
                          "text": a.get("text", "(advert text not found in adverts.jsonl)"),
                          "relevant": row.get("relevant", ""), "fit": row.get("fit", ""), "notes": row.get("notes", "")})
    return items


def save_label(labels_path, job_id, relevant=None, fit=None, notes=None):
    """Update one row of labels.csv; every other row is written back unchanged."""
    labels_path = Path(labels_path)
    if relevant not in (None, "", "yes", "no"):
        raise ValueError("relevant must be yes or no")
    if fit not in (None, "", "1", "2", "3", "4", "5"):
        raise ValueError("fit must be 1-5")
    with LOCK:
        with labels_path.open(newline="") as f:
            rows = list(csv.DictReader(f))
        hit = [r for r in rows if r["id"] == job_id]
        if not hit:
            raise KeyError(job_id)
        r = hit[0]
        if relevant is not None:
            r["relevant"] = relevant
        if fit is not None:
            r["fit"] = fit
        if notes is not None:
            r["notes"] = notes.replace("\n", " ").strip()[:300]
        tmp = labels_path.with_suffix(".tmp")
        with tmp.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        tmp.replace(labels_path)
        done = sum(1 for x in rows if x.get("relevant") in ("yes", "no") and x.get("fit"))
        return {"saved": job_id, "done": done, "total": len(rows)}


PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Seekcrew labelling</title><style>
:root{--bg:#f6f6f4;--card:#fff;--ink:#1d1d1b;--mute:#6b6b66;--line:#ddd;--yes:#1f7a4d;--no:#a33a2c;--sel:#24508f}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--card:#20201e;--ink:#ecebe6;--mute:#9b9a93;--line:#3a3a36;--yes:#4fb985;--no:#e07a6b;--sel:#7aa6e8}}
*{box-sizing:border-box}body{margin:0;font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif;background:var(--bg);color:var(--ink)}
.wrap{display:grid;grid-template-columns:280px 1fr;height:100vh}
aside{border-right:1px solid var(--line);overflow:auto;padding:12px}aside h1{font-size:15px;margin:0 0 4px}
.prog{color:var(--mute);font-size:13px;margin-bottom:10px}.item{padding:6px 8px;border-radius:6px;cursor:pointer;font-size:13px;display:flex;gap:6px}
.item:hover{background:var(--card)}.item.cur{outline:2px solid var(--sel)}.dot{width:10px;height:10px;border-radius:50%;margin-top:5px;flex:none;background:var(--line)}
.dot.yes{background:var(--yes)}.dot.no{background:var(--no)}main{overflow:auto;padding:20px 28px}
.head h2{margin:0;font-size:20px}.meta{color:var(--mute);margin:4px 0 14px}.meta a{color:var(--sel)}
.ctl{position:sticky;top:0;background:var(--bg);padding:10px 0;border-bottom:1px solid var(--line);margin-bottom:14px;display:flex;flex-wrap:wrap;gap:14px;align-items:center}
button{font:inherit;padding:6px 12px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--ink);cursor:pointer}
button.on{border-color:var(--sel);outline:2px solid var(--sel)}.grp{display:flex;gap:6px;align-items:center}.grp span{color:var(--mute);font-size:13px;margin-right:4px}
input[type=text]{font:inherit;padding:6px 8px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--ink);width:280px}
.text{white-space:pre-wrap;background:var(--card);border:1px solid var(--line);border-radius:8px;padding:16px;max-width:900px}
.rub{font-size:12px;color:var(--mute);margin-top:6px}.status{font-size:13px;color:var(--mute)}
@media (max-width:760px){.wrap{grid-template-columns:1fr;height:auto}aside{max-height:30vh;border-right:0;border-bottom:1px solid var(--line)}main{padding:16px}input[type=text]{width:100%}}
</style></head><body><div class="wrap"><aside><h1>Seekcrew labelling</h1><div class="prog" id="prog"></div><div id="list"></div></aside>
<main><div class="ctl"><div class="grp"><span>Relevant</span><button id="ry">Yes (Y)</button><button id="rn">No (N)</button></div>
<div class="grp"><span>Fit</span><button data-f="1">1</button><button data-f="2">2</button><button data-f="3">3</button><button data-f="4">4</button><button data-f="5">5</button></div>
<input type="text" id="notes" placeholder="Notes (optional)"><div class="grp"><button id="prev">&larr; Prev (K)</button><button id="next">Next (J) &rarr;</button></div>
<div class="status" id="status"></div>
<div class="rub">Fit: 5 core requirements match your evidence directly · 4 good, minor gaps · 3 partial, clear gaps · 2 weak · 1 not a match or out of scope. Relevant: you would want it on your shortlist (field, level and location all acceptable).</div></div>
<div class="head"><h2 id="title"></h2><div class="meta" id="meta"></div></div><div class="text" id="text"></div></main></div>
<script>
let items=[],i=0;const $=id=>document.getElementById(id);
async function load(){items=await (await fetch('/api/items')).json();const first=items.findIndex(x=>!(x.relevant&&x.fit));i=first<0?0:first;render()}
function done(){return items.filter(x=>(x.relevant==='yes'||x.relevant==='no')&&x.fit).length}
function render(){const it=items[i];if(!it)return;$('title').textContent=it.title;
 $('meta').textContent='';const m=document.createTextNode(it.company+' · '+it.location+' · '+it.source+' · '+(i+1)+' of '+items.length+' · ');$('meta').appendChild(m);
 if(it.url){const a=document.createElement('a');a.href=it.url;a.target='_blank';a.rel='noopener';a.textContent='open advert';$('meta').appendChild(a)}
 $('text').textContent=it.text;$('notes').value=it.notes||'';$('ry').classList.toggle('on',it.relevant==='yes');$('rn').classList.toggle('on',it.relevant==='no');
 document.querySelectorAll('[data-f]').forEach(b=>b.classList.toggle('on',b.dataset.f===String(it.fit)));
 $('prog').textContent=done()+' of '+items.length+' labelled';const L=$('list');L.textContent='';
 items.forEach((x,k)=>{const d=document.createElement('div');d.className='item'+(k===i?' cur':'');const s=document.createElement('span');s.className='dot '+(x.relevant||'');
  const t=document.createElement('span');t.textContent=x.title+' — '+x.company+(x.fit?' ['+x.fit+']':'');d.append(s,t);d.onclick=()=>{i=k;render();scrollTo(0,0)};L.appendChild(d)});
 const cur=L.children[i];if(cur)cur.scrollIntoView({block:'nearest'})}
async function save(patch){const it=items[i];Object.assign(it,patch);render();
 const r=await fetch('/api/label',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(Object.assign({id:it.id},patch))});
 const j=await r.json();$('status').textContent=r.ok?('Saved · '+j.done+' of '+j.total+' complete'):('Not saved: '+j.error)}
$('ry').onclick=()=>save({relevant:'yes'});$('rn').onclick=()=>save({relevant:'no'});
document.querySelectorAll('[data-f]').forEach(b=>b.onclick=()=>save({fit:b.dataset.f}));
$('notes').onchange=()=>save({notes:$('notes').value});
$('prev').onclick=()=>{if(i>0){i--;render();scrollTo(0,0)}};$('next').onclick=()=>{if(i<items.length-1){i++;render();scrollTo(0,0)}};
document.addEventListener('keydown',e=>{if(e.target.tagName==='INPUT')return;const k=e.key.toLowerCase();
 if(k==='y')save({relevant:'yes'});else if(k==='n')save({relevant:'no'});else if('12345'.includes(k)&&k.length===1)save({fit:k});
 else if(k==='j'||k==='arrowright')$('next').click();else if(k==='k'||k==='arrowleft')$('prev').click()});
load();
</script></body></html>"""


def make_handler(ads_path, labels_path):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/":
                return self._send(200, PAGE, "text/html")
            if self.path == "/api/items":
                return self._send(200, json.dumps(load_items(ads_path, labels_path)))
            self._send(404, json.dumps({"error": "not found"}))

        def do_POST(self):
            if self.path != "/api/label":
                return self._send(404, json.dumps({"error": "not found"}))
            try:
                n = int(self.headers.get("Content-Length") or 0)
                b = json.loads(self.rfile.read(min(n, 100_000)))
                out = save_label(labels_path, b["id"], b.get("relevant"), b.get("fit"), b.get("notes"))
                self._send(200, json.dumps(out))
            except (ValueError, KeyError) as e:
                self._send(400, json.dumps({"error": str(e)}))
    return H


def main(argv=None):
    ap = argparse.ArgumentParser(description="Label the evaluation sample in a local page")
    ap.add_argument("--port", type=int, default=8780)
    ap.add_argument("--adverts", default=str(DATA / "adverts.jsonl"))
    ap.add_argument("--labels", default=str(DATA / "labels.csv"))
    a = ap.parse_args(argv)
    if not Path(a.labels).exists():
        sys.exit("%s not found. Run python3 eval/collect.py first." % a.labels)
    backup = Path(a.labels).with_name("labels.backup.csv")
    shutil.copy2(a.labels, backup)
    print("Backup of your labels: %s" % backup)
    print("Labelling page: http://127.0.0.1:%d  (Ctrl+C to stop; every change is saved immediately)" % a.port)
    try:
        ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(a.adverts, a.labels)).serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
