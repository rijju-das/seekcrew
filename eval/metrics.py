"""Evaluation metrics, standard library only."""
import math
import random
import re
import statistics


def confusion(pred, gold):
    """pred, gold: dicts id -> bool (pred may miss ids: counted as not relevant)."""
    tp = fp = fn = tn = 0
    for i, g in gold.items():
        p = bool(pred.get(i, False))
        if p and g:
            tp += 1
        elif p and not g:
            fp += 1
        elif not p and g:
            fn += 1
        else:
            tn += 1
    return tp, fp, fn, tn


def prf(tp, fp, fn, tn=0):
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    acc = (tp + tn) / (tp + fp + fn + tn) if tp + fp + fn + tn else 0.0
    return {"precision": p, "recall": r, "f1": f, "accuracy": acc}


def ranks(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            r[order[k]] = avg
        i = j + 1
    return r


def pearson(a, b):
    if len(a) < 2:
        return float("nan")
    ma, mb = statistics.fmean(a), statistics.fmean(b)
    sa = math.sqrt(sum((x - ma) ** 2 for x in a))
    sb = math.sqrt(sum((y - mb) ** 2 for y in b))
    if sa == 0 or sb == 0:
        return float("nan")
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (sa * sb)


def spearman(a, b):
    return pearson(ranks(a), ranks(b))


def quadratic_kappa(a, b, lo=1, hi=5):
    """Quadratic weighted Cohen's kappa for integer ratings in [lo, hi]."""
    n = len(a)
    if n == 0:
        return float("nan")
    k = hi - lo + 1
    obs = [[0] * k for _ in range(k)]
    for x, y in zip(a, b):
        obs[x - lo][y - lo] += 1
    ha = [sum(row) for row in obs]
    hb = [sum(obs[i][j] for i in range(k)) for j in range(k)]
    num = den = 0.0
    for i in range(k):
        for j in range(k):
            w = (i - j) ** 2 / (k - 1) ** 2
            num += w * obs[i][j]
            den += w * ha[i] * hb[j] / n
    return 1 - num / den if den else float("nan")


def fit_agreement(pred_fit, gold_fit):
    ids = [i for i in gold_fit if i in pred_fit]
    a = [pred_fit[i] for i in ids]
    b = [gold_fit[i] for i in ids]
    if not ids:
        return {"n": 0}
    return {"n": len(ids), "mae": statistics.fmean(abs(x - y) for x, y in zip(a, b)),
            "exact": statistics.fmean(1.0 if x == y else 0.0 for x, y in zip(a, b)),
            "within_1": statistics.fmean(1.0 if abs(x - y) <= 1 else 0.0 for x, y in zip(a, b)),
            "spearman": spearman(a, b), "qwk": quadratic_kappa(a, b)}


def bootstrap_f1(pred, gold, n=2000, seed=7):
    """95% percentile bootstrap interval for F1, resampling jobs."""
    ids = list(gold)
    rng = random.Random(seed)
    vals = []
    for _ in range(n):
        s = [rng.choice(ids) for _ in ids]
        tp = sum(1 for i in s if pred.get(i) and gold[i])
        fp = sum(1 for i in s if pred.get(i) and not gold[i])
        fn = sum(1 for i in s if not pred.get(i) and gold[i])
        vals.append(prf(tp, fp, fn)["f1"])
    vals.sort()
    return vals[int(0.025 * n)], vals[int(0.975 * n) - 1]


def pairwise_agreement(runs):
    """Mean share of jobs on which two runs make the same relevant/not-relevant call."""
    if len(runs) < 2:
        return None
    vals = []
    for x in range(len(runs)):
        for y in range(x + 1, len(runs)):
            ids = set(runs[x]) | set(runs[y])
            vals.append(statistics.fmean(1.0 if bool(runs[x].get(i)) == bool(runs[y].get(i)) else 0.0 for i in ids))
    return statistics.fmean(vals)


def _norm(t):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9%.+# ]", " ", (t or "").lower())).strip()


def evidence_grounding(decisions, evidence_text):
    """Share of evidence quotes that really appear in the evidence bank (after normalising case,
    punctuation and spacing). Quotes that do not appear are counted as ungrounded."""
    bank = _norm(evidence_text)
    quotes = [q for d in decisions.values() for q in d.get("evidence", []) if q.strip()]
    if not quotes:
        return {"quotes": 0, "grounded": None}
    ok = sum(1 for q in quotes if _norm(q) and _norm(q) in bank)
    return {"quotes": len(quotes), "grounded": ok / len(quotes)}
