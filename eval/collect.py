#!/usr/bin/env python3
"""Collect and freeze a sample of real job adverts for the Seekcrew evaluation.

Run this on your own computer (it needs normal internet access):

    python3 eval/collect.py                    # uses eval/boards.json, or boards.example.json
    python3 eval/collect.py --sample-size 25
    python3 eval/collect.py --add-manual       # add new adverts from eval/manual/ to the frozen sample

Adverts you found yourself (for example on LinkedIn or HiringCafe, which have no public API and
must not be scraped) can be added by hand: save each one as a .txt file in eval/manual/ in the
format of eval/manual_template.txt. They are added on top of the board sample, marked source
"manual", and the evaluation report counts adverts per source. --add-manual appends only adverts
that are not in the sample yet and never changes existing rows or labels.

It reads public Greenhouse, Lever and Ashby job-board APIs, keeps adverts whose location
matches your patterns, draws a seeded sample that mixes technical and non-technical titles,
and writes two files under eval/data/ (git-ignored, because advert text belongs to the employers):

    eval/data/adverts.jsonl   one advert per line: id, company, title, location, url, text, source
    eval/data/labels.csv      a sheet for you to label: relevant (yes/no) and fit (1-5)

The sample is frozen: running the evaluation again uses the same adverts, so results are
comparable across systems and over time. Use --force to replace an existing sample.
Standard library only.
"""
import argparse
import csv
import datetime
import hashlib
import html
import json
import random
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
UA = "seekcrew-eval/1.0 (+https://github.com/rijju-das/seekcrew)"


def board_url(provider, token):
    if provider == "greenhouse":
        return "https://boards-api.greenhouse.io/v1/boards/%s/jobs?content=true" % token
    if provider == "lever":
        return "https://api.lever.co/v0/postings/%s?mode=json" % token
    if provider == "ashby":
        return "https://api.ashbyhq.com/posting-api/job-board/%s" % token
    raise ValueError("unknown provider %r" % provider)


def fetch_json(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def plain(text):
    """HTML (possibly escaped twice, as Greenhouse does) to readable plain text."""
    t = html.unescape(html.unescape(text or ""))
    t = re.sub(r"(?i)<\s*(br|/p|/li|/h\d|/div)\s*/?>", "\n", t)
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"[ \t\r\f\v]+", " ", t)
    t = re.sub(r"\n\s*\n+", "\n", t)
    return t.strip()


def normalise(provider, company, item):
    """Map one provider record to the common advert shape. Returns None if unusable."""
    if provider == "greenhouse":
        jid, title = item.get("id"), item.get("title")
        loc = (item.get("location") or {}).get("name", "")
        url, text = item.get("absolute_url"), plain(item.get("content", ""))
    elif provider == "lever":
        jid, title = item.get("id"), item.get("text")
        cats = item.get("categories") or {}
        loc = cats.get("location") or ", ".join(cats.get("allLocations") or [])
        url = item.get("hostedUrl")
        parts = [item.get("descriptionPlain") or plain(item.get("description", ""))]
        for block in item.get("lists") or []:
            parts.append((block.get("text") or "") + "\n" + plain(block.get("content", "")))
        parts.append(item.get("additionalPlain") or "")
        text = "\n".join(p for p in parts if p).strip()
    elif provider == "ashby":
        if item.get("isListed") is False:
            return None
        jid, title = item.get("id") or item.get("jobUrl"), item.get("title")
        loc = item.get("location") or ""
        if isinstance(loc, dict):
            loc = loc.get("name", "")
        extra = [a.get("location") for a in item.get("secondaryLocations") or [] if isinstance(a, dict)]
        if extra:
            loc = ", ".join([loc] + [e for e in extra if e])
        if item.get("isRemote") and "remote" not in loc.lower():
            loc = (loc + ", Remote").strip(", ")
        url = item.get("jobUrl")
        text = item.get("descriptionPlain") or plain(item.get("descriptionHtml", ""))
    else:
        return None
    if not (jid and title and url):
        return None
    return {"id": "%s-%s-%s" % (provider, re.sub(r"\W+", "", company.lower()), jid),
            "company": company, "title": title.strip(), "location": (loc or "").strip(),
            "url": url, "text": text, "source": provider}


HEADER_KEYS = {"company": "company", "title": "title", "location": "location", "url": "url", "link": "url"}


def read_manual(path):
    """Parse one manual advert: 'Key: value' header lines, a blank line, then the advert text."""
    raw = Path(path).read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
    head, _, body = raw.partition("\n\n")
    fields = {}
    for line in head.splitlines():
        key, sep, val = line.partition(":")
        k = HEADER_KEYS.get(key.strip().lower())
        if sep and k:
            fields[k] = val.strip()
    missing = [k for k in ("company", "title") if not fields.get(k)]
    if missing:
        raise ValueError("%s: missing %s (see eval/manual_template.txt)" % (Path(path).name, " and ".join(missing)))
    text = body.strip()
    if len(text) < 200:
        raise ValueError("%s: the advert text is very short (%d characters); paste the full advert below the blank line" % (Path(path).name, len(text)))
    key = fields.get("url") or Path(path).name
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]
    return {"id": "manual-%s-%s" % (re.sub(r"\W+", "", fields["company"].lower())[:20], digest),
            "company": fields["company"], "title": fields["title"], "location": fields.get("location", ""),
            "url": fields.get("url", ""), "text": text, "source": "manual", "file": Path(path).name}


def manual_adverts(folder):
    ads, problems = [], []
    folder = Path(folder)
    if not folder.exists():
        return ads, problems
    for f in sorted(folder.glob("*.txt")) + sorted(folder.glob("*.md")):
        try:
            ads.append(read_manual(f))
        except (ValueError, OSError) as e:
            problems.append(str(e))
    return ads, problems


def same_job(a, b):
    ua, ub = (a.get("url") or "").rstrip("/").lower(), (b.get("url") or "").rstrip("/").lower()
    return a["id"] == b["id"] or (ua and ua == ub)


def write_sample(out_ads, out_labels, ads, append=False):
    stamp = datetime.datetime.now().isoformat(timespec="seconds")
    with out_ads.open("a" if append else "w") as f:
        for x in ads:
            f.write(json.dumps(dict(x, collected=stamp)) + "\n")
    new_file = not (append and out_labels.exists())
    with out_labels.open("a" if not new_file else "w", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["id", "company", "title", "location", "url", "relevant", "fit", "notes"])
        for x in ads:
            w.writerow([x["id"], x["company"], x["title"], x["location"], x["url"], "", "", ""])


def add_manual_only(manual_dir):
    out_ads, out_labels = DATA / "adverts.jsonl", DATA / "labels.csv"
    if not out_ads.exists():
        sys.exit("No frozen sample yet. Run python3 eval/collect.py first.")
    existing = [json.loads(l) for l in out_ads.read_text().splitlines() if l.strip()]
    ads, problems = manual_adverts(manual_dir)
    for p in problems:
        print("  skipped: " + p)
    new = [a for a in ads if not any(same_job(a, e) for e in existing)]
    if not new:
        print("No new manual adverts to add (%d found, all already in the sample)." % len(ads))
        return
    write_sample(out_ads, out_labels, new, append=True)
    print("Added %d manual adverts to the sample (%d already there). Label the new rows at the end of %s." % (
        len(new), len(ads) - len(new), out_labels))


def location_ok(loc, include, exclude):
    low = (loc or "").lower()
    if any(re.search(p, low) for p in exclude):
        return False
    return any(re.search(p, low) for p in include)


def sample(pool, size, technical_share, tech_pattern, seed):
    rng = random.Random(seed)
    pool = sorted(pool, key=lambda a: a["id"])  # stable order before shuffling
    tech = [a for a in pool if re.search(tech_pattern, a["title"].lower())]
    other = [a for a in pool if a not in tech]
    rng.shuffle(tech)
    rng.shuffle(other)
    n_tech = min(len(tech), round(size * technical_share))
    n_other = min(len(other), size - n_tech)
    n_tech = min(len(tech), size - n_other)  # top up from technical if there are too few others
    picked = tech[:n_tech] + other[:n_other]
    rng.shuffle(picked)
    return picked


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--boards", default=None, help="boards file (default eval/boards.json, else the example)")
    ap.add_argument("--sample-size", type=int, default=None)
    ap.add_argument("--force", action="store_true", help="replace an existing frozen sample")
    ap.add_argument("--manual-dir", default=str(HERE / "manual"), help="folder of hand-saved adverts (default eval/manual)")
    ap.add_argument("--add-manual", action="store_true", help="only add new manual adverts to the existing sample")
    a = ap.parse_args(argv)
    if a.add_manual:
        return add_manual_only(a.manual_dir)

    bpath = Path(a.boards) if a.boards else (HERE / "boards.json" if (HERE / "boards.json").exists() else HERE / "boards.example.json")
    cfg = json.loads(bpath.read_text())
    size = a.sample_size or cfg.get("sample_size", 60)
    out_ads, out_labels = DATA / "adverts.jsonl", DATA / "labels.csv"
    if out_ads.exists() and not a.force:
        sys.exit("%s already exists. The sample is frozen so results stay comparable. Use --force to replace it." % out_ads)

    pool, report = [], []
    for b in cfg["boards"]:
        url = board_url(b["provider"], b["token"])
        try:
            data = fetch_json(url)
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            report.append("%-14s %-10s not readable (%s)" % (b["company"], b["provider"], getattr(e, "reason", e)))
            continue
        items = data if isinstance(data, list) else data.get("jobs", [])
        ads = [x for x in (normalise(b["provider"], b["company"], i) for i in items) if x]
        kept = [x for x in ads if location_ok(x["location"], cfg["location_patterns"], cfg.get("exclude_location_patterns", []))]
        pool.extend(kept)
        report.append("%-14s %-10s %4d adverts, %3d in scope" % (b["company"], b["provider"], len(ads), len(kept)))

    print("Boards:")
    print("\n".join("  " + r for r in report))
    seen, unique = set(), []
    for x in pool:
        if x["id"] not in seen:
            seen.add(x["id"])
            unique.append(x)
    picked = sample(unique, size, cfg.get("technical_share", 0.6), cfg["technical_title_pattern"], cfg.get("seed", 13))
    if not picked:
        sys.exit("No adverts matched. Check the boards and location patterns.")

    manual, problems = manual_adverts(a.manual_dir)
    for p in problems:
        print("  manual advert skipped: " + p)
    manual = [m for m in manual if not any(same_job(m, x) for x in picked)]
    DATA.mkdir(exist_ok=True)
    write_sample(out_ads, out_labels, picked + manual)
    tech = sum(1 for x in picked if re.search(cfg["technical_title_pattern"], x["title"].lower()))
    print("\nIn scope: %d unique adverts. Sampled %d from boards (seed %s; %d with titles in your field), plus %d manual." % (
        len(unique), len(picked), cfg.get("seed", 13), tech, len(manual)))
    print("Wrote %s and %s" % (out_ads, out_labels))
    print("Next: open eval/data/labels.csv, fill in 'relevant' (yes/no) and 'fit' (1-5) for every row, save as CSV.")


if __name__ == "__main__":
    main()
