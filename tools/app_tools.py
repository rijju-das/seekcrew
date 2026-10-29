#!/usr/bin/env python3
"""Duplicate check, folder creation and tracker rows for job applications.

Used by the Claude Code agents in this repository. Python 3.8+, standard library only.

  python3 tools/app_tools.py init
  python3 tools/app_tools.py check --employer "Acme Ltd" --role "Applied Scientist" \
      --url "https://..." --job-id 12345
  python3 tools/app_tools.py next
  python3 tools/app_tools.py new-folder --name Acme_Applied_Scientist --employer "Acme Ltd" \
      --role "Applied Scientist" --url "https://..."
  python3 tools/app_tools.py tracker-add --company "Acme Ltd" --role "Applied Scientist" \
      --type Industry --location "Remote" --mode Hybrid --source "Company website" \
      --url "https://..." --deadline 2026-11-10 --fit 4 --priority High \
      --next-action "Review the pack, then apply" --documents "applications/01_Acme_Applied_Scientist"

init:         copies the *.example files to PROFILE.md, EVIDENCE_BANK.md, tracker.csv and
              seekcrew.config.json (only if they do not exist) and creates applications/, shortlists/, master/.
check:        compares a job with every folder in applications/ (and the Job_Requirements.md inside each)
              and every row of tracker.csv. Verdict:
                NEW        no match found                              (exit code 0)
                POSSIBLE   same employer, possibly the same role        (exit code 1) - a person decides
                DUPLICATE  same URL / job ID, or same employer and role (exit code 2)
new-folder:   runs the check first, refuses on DUPLICATE or POSSIBLE unless --force is given,
              then creates applications/NN_<name>/ with the next number and prints the path.
next:         prints the next folder number (highest number in applications/ plus one).
tracker-add:  appends one row to tracker.csv with the next unused permanent ID. Checks the tracker
              for duplicates first (refuses unless --force), backs the file up to .backups/ and
              restores it if the write cannot be verified. Never changes existing rows.

Optional settings live in seekcrew.config.json (see seekcrew.config.example.json): your own
application types, work modes, sources, statuses, priorities, name aliases ("phrases") and
words to ignore when comparing employer names.
Set SEEKCREW_ROOT to point the tool at another workspace folder.
"""
import argparse
import csv
import datetime
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(os.environ.get("SEEKCREW_ROOT") or Path(__file__).resolve().parents[1])
APPS = ROOT / "applications"
TRACKER = ROOT / "tracker.csv"
BACKUPS = ROOT / ".backups"
CONFIG = ROOT / "seekcrew.config.json"

HEADERS = ["ID", "Company", "Role", "Application type", "Location", "Work mode", "Source", "Job link",
           "Date found", "Deadline", "Date applied", "Status", "Fit (1-5)", "Priority", "Contact",
           "Contact details", "Next action", "Follow-up date", "Documents used", "Notes"]

DEFAULTS = {
    "types": ["Industry", "Research", "Academic", "Internship", "Contract", "Other"],
    "modes": ["", "On-site", "Hybrid", "Remote"],
    "sources": ["LinkedIn", "Company website", "Job board", "Referral", "Recruiter", "Other"],
    "statuses": ["Saved", "Preparing", "On hold", "Not pursuing", "Applied", "Screening", "Interview",
                 "Assignment", "Reference check", "Offer", "Rejected", "Withdrawn", "Closed"],
    "priorities": ["", "High", "Medium", "Low"],
    # [phrase, token]: different spellings of one employer become one token, e.g. ["massachusetts institute of technology", "mit"]
    "phrases": [],
    # single-word aliases, e.g. {"msft": "microsoft"}
    "aliases": {},
    # extra words to ignore when comparing employer names (location words, legal forms)
    "employer_generic": [],
}

STOP = set("""a an the of and or for to in at on with by from as is are be this that
university college school department dept institute centre center research limited ltd inc llc
plc group company dublin ireland irish europe emea remote hybrid full time part fixed term contract
role post position job vacancy vacancies opportunity""".split())
# words that are too generic to identify a role on their own
ROLE_STOP = set("""senior junior lead principal staff associate assistant ii iii i engineer scientist
researcher postdoctoral postdoc fellow programme program project""".split())
EMP_GENERIC = set("computer science sciences engineering applied artificial intelligence school faculty".split())


def config():
    cfg = dict(DEFAULTS)
    if CONFIG.exists():
        try:
            user = json.loads(CONFIG.read_text(encoding="utf-8"))
        except ValueError as e:
            raise SystemExit("seekcrew.config.json is not valid JSON: %s" % e)
        for k, v in user.items():
            if k in cfg:
                cfg[k] = v
    return cfg


# ---------------------------------------------------------------- text matching helpers
def words(text, cfg=None):
    cfg = cfg or config()
    text = (text or "").lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = " " + text.strip() + " "
    for phrase, token in cfg["phrases"]:
        text = text.replace(" " + phrase.lower() + " ", " " + token.lower() + " ")
    aliases = dict((k.lower(), v.lower()) for k, v in cfg["aliases"].items())
    out = []
    for w in text.split():
        out.extend(aliases.get(w, w).split())
    return out


def tokens(text, extra_stop=(), cfg=None):
    return set(w for w in words(text, cfg) if w not in STOP and w not in extra_stop and len(w) > 1)


def overlap(a, b):
    """Share of the smaller set that appears in the other (good for short names)."""
    if not a or not b:
        return 0.0
    return len(a & b) / float(min(len(a), len(b)))


def coverage(want, have):
    """Share of the wanted words that appear in the existing record."""
    if not want or not have:
        return 0.0
    return len(want & have) / float(len(want))


def ids_in(text):
    """Job IDs and reference numbers: runs of 5 or more digits, or letters+digits codes."""
    found = set(re.findall(r"(?<![0-9])[0-9]{5,}(?![0-9])", text or ""))
    found |= set(m.lower() for m in re.findall(r"\b[A-Za-z]{2,5}[-_ ]?[0-9]{3,}\b", text or ""))
    return found


def norm_url(url):
    u = (url or "").strip().lower()
    u = re.sub(r"^https?://(www\.)?", "", u)
    u = u.split("#")[0].split("?")[0].rstrip("/")
    return u


# ---------------------------------------------------------------- reading folders and tracker
def folder_records():
    recs = []
    if not APPS.exists():
        return recs
    for d in sorted(APPS.iterdir()):
        if not d.is_dir() or not re.match(r"^\d+_", d.name):
            continue
        req = d / "Job_Requirements.md"
        text = ""
        if req.exists():
            try:
                text = req.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
        head = "\n".join(text.splitlines()[:40])
        recs.append({"kind": "folder", "where": str(d.relative_to(ROOT)), "name": d.name,
                     "head": head, "text": text})
    return recs


def csv_rows():
    if not TRACKER.exists():
        return []
    with open(str(TRACKER), newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def tracker_records():
    recs = []
    for row in csv_rows():
        recs.append({"kind": "tracker", "where": "tracker ID %s (%s)" % (row.get("ID", "?"), row.get("Status", "")),
                     "employer": row.get("Company", ""), "role": row.get("Role", ""),
                     "url": row.get("Job link", ""), "text": " ".join(str(v) for v in row.values())})
    return recs


# ---------------------------------------------------------------- duplicate check
def check(employer, role, url, job_id, use_folders=True):
    cfg = config()
    emp_generic = EMP_GENERIC | set(w.lower() for w in cfg["employer_generic"])
    want_url = norm_url(url)
    want_ids = set()
    if job_id:
        want_ids.add(job_id.strip().lower())
    want_ids |= set(i for i in ids_in(url) if len(i) >= 6)
    emp_t = tokens(employer, emp_generic, cfg)
    role_t = tokens(role, ROLE_STOP, cfg)
    generic_role = len(role_t) < 2  # only generic words such as "research assistant": cannot prove a duplicate
    if not role_t:
        role_t = tokens(role, (), cfg)
    matches = []
    related = []  # same employer but clearly a different job: shown for information, never blocks

    def consider(rec, emp_text, role_text, rec_url, rec_text):
        reasons, level = [], 0
        if want_url and rec_url and norm_url(rec_url) == want_url:
            reasons.append("same URL")
            level = 2
        low = (rec_text or "").lower()
        if want_url and level < 2 and want_url in re.sub(r"https?://(www\.)?", "", low):
            reasons.append("same URL appears in the record")
            level = 2
        for i in want_ids:
            if i and re.search(r"(?<![a-z0-9])" + re.escape(i) + r"(?![a-z0-9])", low):
                reasons.append("same job ID/reference %s" % i)
                level = 2
        e = max(overlap(emp_t, tokens(emp_text, emp_generic, cfg)), 0.0)
        r = coverage(role_t, tokens(role_text, ROLE_STOP, cfg) or tokens(role_text, (), cfg))
        if level < 2 and e >= 0.5:
            if r >= 0.75 and not generic_role:
                reasons.append("same employer and similar role (employer %.0f%%, role %.0f%%)" % (e * 100, r * 100))
                level = 2
            elif generic_role or r >= 0.34:
                reasons.append("same employer, possibly the same role (employer %.0f%%, role %.0f%%)" % (e * 100, r * 100))
                level = 1
            else:
                related.append((rec, "same employer, clearly different role (employer %.0f%%, role %.0f%%)" % (e * 100, r * 100)))
        elif level < 2 and r >= 0.8 and e >= 0.25 and not generic_role:
            reasons.append("very similar role title, employer partly matches")
            level = 1
        if level:
            matches.append((level, rec, reasons))

    if use_folders:
        for rec in folder_records():
            head = rec["head"]
            title = head.splitlines()[0] if head else ""
            consider(rec, rec["name"] + " " + title, title + " " + rec["name"], "", rec["text"])
    for rec in tracker_records():
        consider(rec, rec["employer"], rec["role"], rec["url"], rec["text"])
    matches.sort(key=lambda m: -m[0])
    verdict = "NEW"
    if matches:
        verdict = "DUPLICATE" if matches[0][0] == 2 else "POSSIBLE"
    check.related = related
    return verdict, matches


def next_number():
    top = 0
    if APPS.exists():
        for d in APPS.iterdir():
            m = re.match(r"^(\d+)_", d.name)
            if d.is_dir() and m:
                top = max(top, int(m.group(1)))
    return top + 1


def report(verdict, matches):
    print("VERDICT: " + verdict)
    seen = set()
    for level, rec, reasons in matches[:6]:
        key = rec["where"]
        if key in seen:
            continue
        seen.add(key)
        print("  - %s: %s" % (rec["where"], "; ".join(reasons)))
    if verdict == "NEW":
        print("  No folder or tracker row is the same job (same URL, job ID, or employer plus role).")
    shown = set()
    for rec, why in getattr(check, "related", [])[:4]:
        if rec["where"] in shown:
            continue
        shown.add(rec["where"])
        print("  (related, not a duplicate) %s: %s" % (rec["where"], why))


# ---------------------------------------------------------------- adding a tracker row
def parse_date(label, value):
    if not value:
        return ""
    try:
        datetime.datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise SystemExit("%s must be YYYY-MM-DD or empty (got %r). Put free text in --notes." % (label, value))
    return value


def next_tracker_id():
    ids = []
    for row in csv_rows():
        try:
            ids.append(int(row.get("ID", "")))
        except ValueError:
            pass
    return (max(ids) if ids else 0) + 1


def write_csv(path, rows):
    with open(str(path), "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, quoting=csv.QUOTE_ALL)
        w.writerow(HEADERS)
        for r in rows:
            w.writerow([r.get(h, "") for h in HEADERS])


def add_tracker_row(f):
    cfg = config()
    for label, val, allowed in (("--type", f["Application type"], cfg["types"]), ("--mode", f["Work mode"], cfg["modes"]),
                                ("--source", f["Source"], cfg["sources"]), ("--status", f["Status"], cfg["statuses"]),
                                ("--priority", f["Priority"], cfg["priorities"])):
        if val not in allowed:
            raise SystemExit("%s must be one of: %s (got %r)" % (label, ", ".join(a for a in allowed if a), val))
    if f["Fit (1-5)"] and f["Fit (1-5)"] not in ("1", "2", "3", "4", "5"):
        raise SystemExit("--fit must be 1 to 5")

    existing = csv_rows()
    new_id = next_tracker_id()
    f = dict(f)
    f["ID"] = str(new_id)

    BACKUPS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    backup = BACKUPS / ("tracker_before_%s.csv" % stamp)
    had_file = TRACKER.exists()
    if had_file:
        shutil.copy2(str(TRACKER), str(backup))
    try:
        fd, tmp = tempfile.mkstemp(suffix=".csv", dir=str(ROOT))
        os.close(fd)
        write_csv(tmp, existing + [f])
        os.replace(tmp, str(TRACKER))
        after = csv_rows()
        ids = [str(r.get("ID", "")).strip() for r in after]
        mine = [r for r in after if str(r.get("ID", "")).strip() == str(new_id)]
        if len(after) != len(existing) + 1 or len(mine) != 1 or mine[0].get("Company") != f["Company"]:
            raise RuntimeError("new row not found after writing")
        if len(ids) != len(set(ids)):
            raise RuntimeError("IDs are not unique after writing")
        for old, new in zip(existing, after):
            if any(old.get(h, "") != new.get(h, "") for h in HEADERS):
                raise RuntimeError("an existing row changed (ID %s)" % old.get("ID"))
    except Exception as e:
        if had_file:
            shutil.copy2(str(backup), str(TRACKER))
        elif TRACKER.exists():
            TRACKER.unlink()
        raise SystemExit("tracker-add FAILED and the tracker was restored: %s" % e)
    print("TRACKER ROW ADDED: ID %d (%s - %s, status %s)" % (new_id, f["Company"], f["Role"], f["Status"]))
    if had_file:
        print("Backup: %s" % backup.relative_to(ROOT))
    return 0


# ---------------------------------------------------------------- workspace setup
def init_workspace():
    pairs = [("PROFILE.example.md", "PROFILE.md"), ("EVIDENCE_BANK.example.md", "EVIDENCE_BANK.md"),
             ("tracker.example.csv", "tracker.csv"), ("seekcrew.config.example.json", "seekcrew.config.json")]
    for src, dst in pairs:
        s, d = ROOT / src, ROOT / dst
        if d.exists():
            print("kept     %s (already exists)" % dst)
        elif s.exists():
            shutil.copy2(str(s), str(d))
            print("created  %s" % dst)
        else:
            print("missing  %s (cannot create %s)" % (src, dst))
    for folder in ("applications", "shortlists", "master"):
        (ROOT / folder).mkdir(exist_ok=True)
    print("Next: edit PROFILE.md and EVIDENCE_BANK.md, put your CV in master/, then start Claude Code here.")
    return 0


# ---------------------------------------------------------------- command line
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("check", "new-folder"):
        p = sub.add_parser(name)
        p.add_argument("--employer", default="")
        p.add_argument("--role", default="")
        p.add_argument("--url", default="")
        p.add_argument("--job-id", default="")
        if name == "new-folder":
            p.add_argument("--name", required=True, help="Short name after the number, e.g. Acme_Applied_Scientist")
            p.add_argument("--force", action="store_true", help="Create even if the check is not NEW")
    sub.add_parser("next")
    sub.add_parser("init")
    cfg = config()
    t = sub.add_parser("tracker-add")
    t.add_argument("--company", required=True)
    t.add_argument("--role", required=True)
    t.add_argument("--type", required=True, help="; ".join(cfg["types"]))
    t.add_argument("--location", default="")
    t.add_argument("--mode", default="", help="On-site, Hybrid or Remote (leave empty if not stated)")
    t.add_argument("--source", default="Other", help="; ".join(cfg["sources"]))
    t.add_argument("--url", default="")
    t.add_argument("--job-id", default="")
    t.add_argument("--found", default=datetime.date.today().isoformat(), help="YYYY-MM-DD (default today)")
    t.add_argument("--deadline", default="", help="YYYY-MM-DD, empty if not stated")
    t.add_argument("--status", default="Preparing")
    t.add_argument("--fit", default="")
    t.add_argument("--priority", default="")
    t.add_argument("--contact", default="")
    t.add_argument("--contact-details", default="")
    t.add_argument("--next-action", default="")
    t.add_argument("--documents", default="", help="Relative folder path, e.g. applications/01_Acme_Role")
    t.add_argument("--notes", default="")
    t.add_argument("--force", action="store_true", help="Add even if the tracker already has a similar row")
    a = ap.parse_args()

    if a.cmd == "init":
        return init_workspace()
    if a.cmd == "next":
        print("%02d" % next_number())
        return 0

    if a.cmd == "tracker-add":
        verdict, matches = check(a.company, a.role, a.url, a.job_id, use_folders=False)
        report(verdict, matches)
        if verdict != "NEW" and not a.force:
            print("Tracker row NOT added. The tracker already has a matching row (use --force only if the user confirms it is a different job).")
            return {"POSSIBLE": 1, "DUPLICATE": 2}[verdict]
        notes = a.notes
        if a.job_id and a.job_id not in notes:
            notes = (notes + " Job ID/reference: %s." % a.job_id).strip()
        fields = {"ID": "", "Company": a.company, "Role": a.role, "Application type": a.type,
                  "Location": a.location, "Work mode": a.mode, "Source": a.source, "Job link": a.url,
                  "Date found": parse_date("--found", a.found), "Deadline": parse_date("--deadline", a.deadline),
                  "Date applied": "", "Status": a.status, "Fit (1-5)": a.fit, "Priority": a.priority,
                  "Contact": a.contact, "Contact details": a.contact_details, "Next action": a.next_action,
                  "Follow-up date": "", "Documents used": a.documents, "Notes": notes}
        return add_tracker_row(fields)

    if not (a.employer or a.url or a.job_id):
        print("Give at least --employer, --url or --job-id", file=sys.stderr)
        return 3
    verdict, matches = check(a.employer, a.role, a.url, a.job_id)
    report(verdict, matches)
    if a.cmd == "check":
        return {"NEW": 0, "POSSIBLE": 1, "DUPLICATE": 2}[verdict]

    if verdict != "NEW" and not a.force:
        print("Folder NOT created. Review the matches above (use --force only if the user confirms it is a different job).")
        return {"POSSIBLE": 1, "DUPLICATE": 2}[verdict]
    name = re.sub(r"[^A-Za-z0-9_]+", "_", a.name).strip("_")
    if not name:
        print("Invalid --name", file=sys.stderr)
        return 3
    target = APPS / ("%02d_%s" % (next_number(), name))
    target.mkdir(parents=True, exist_ok=False)
    print("CREATED: " + str(target.relative_to(ROOT)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
