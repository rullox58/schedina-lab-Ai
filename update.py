#!/usr/bin/env python3
"""Aggiorna data.json con partite, quote e probabilità.

Calendari e quote: The Odds API (chiave nella variabile ODDS_API_KEY).
Calcio: modello di Poisson sui gol storici di football-data.co.uk.
Tennis e basket: probabilità dal consenso dei bookmaker (nessuna statistica indipendente).
"""
import csv, difflib, io, json, math, os, re, sys, unicodedata
import urllib.parse, urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

KEY = os.environ.get("ODDS_API_KEY", "")
BASE = "https://api.the-odds-api.com/v4"
REGIONS = "eu"                    # regioni dei bookmaker (ogni regione costa crediti)
ROME = ZoneInfo("Europe/Rome")
WINDOW_H = 30                     # solo partite nelle prossime 30 ore
MIN_CREDITS = 20                  # ci si ferma se i crediti residui scendono sotto
SEASONS = ["2526", "2627"]        # stagioni per le statistiche: aggiorna ogni agosto
FOOTBALL_MARKETS = "h2h,totals"   # togli ",totals" per risparmiare crediti
MARGIN = 1.06                     # margine usato per le quote stimate (set nel tennis)

FOOTBALL = {  # chiave The Odds API: (codice lega nell'app, file football-data.co.uk)
    "soccer_italy_serie_a": ("SA", "I1"),
    "soccer_italy_serie_b": ("SB", "I2"),
    "soccer_epl": ("PL", "E0"),
    "soccer_efl_champ": ("CH", "E1"),
    "soccer_england_league1": ("E1", "E2"),
    "soccer_england_league2": ("E2", "E3"),
    "soccer_spl": ("SC", "SC0"),
    "soccer_france_ligue_one": ("L1", "F1"),
    "soccer_spain_la_liga": ("LL", "SP1"),
    "soccer_portugal_primeira_liga": ("PT", "P1"),
    "soccer_netherlands_eredivisie": ("ER", "N1"),
    "soccer_germany_bundesliga": ("BL", "D1"),
    "soccer_germany_bundesliga2": ("B2", "D2"),
    "soccer_sweden_allsvenskan": ("SW", "SWE"),
    "soccer_norway_eliteserien": ("NO", "NOR"),
    "soccer_denmark_superliga": ("DK", "DNK"),
}
EUROPE = {  # coppe europee: nessuna statistica comune, probabilità dal consenso dei bookmaker
    "soccer_uefa_champs_league": "CL",
    "soccer_uefa_europa_league": "EU",
    "soccer_uefa_europa_conference_league": "CO",  # chiave non confermata: se non esiste viene saltata
}
NEW = {"SWE", "NOR", "DNK"}  # campionati "extra" di football-data.co.uk (file e formato diversi)
BASKET = {"basketball_nba": "NBA"}
TENNIS = {"tennis_atp": "ATP", "tennis_wta": "WTA", "tennis_itf": "ITF", "tennis_challenger": "CHL"}
PRECHECK = True  # controlla prima, senza spendere crediti, se la lega ha partite nella finestra
GRAND_SLAMS = ("australian open", "french open", "roland garros", "wimbledon", "us open")
STOP = {"fc", "cf", "afc", "ac", "as", "ss", "ssc", "sc", "us", "ud", "cd", "rc", "rcd", "fk", "1", "club", "de", "the"}
ALIAS = {  # nome The Odds API (minuscolo, senza accenti) -> nome in football-data
    "internazionale milano": "inter", "inter milan": "inter", "ac milan": "milan",
    "manchester united": "man united", "manchester city": "man city",
    "tottenham hotspur": "tottenham", "newcastle united": "newcastle",
    "wolverhampton wanderers": "wolves", "brighton and hove albion": "brighton",
    "west ham united": "west ham", "nottingham forest": "nott'm forest",
    "leeds united": "leeds", "leicester city": "leicester",
    "atletico madrid": "ath madrid", "athletic bilbao": "ath bilbao",
    "athletic club": "ath bilbao", "real betis": "betis", "real sociedad": "sociedad",
    "celta vigo": "celta", "paris saint germain": "paris sg", "paris saint-germain": "paris sg",
    "olympique marseille": "marseille", "olympique lyonnais": "lyon", "as monaco": "monaco",
    "sporting cp": "sp lisbon", "sporting braga": "sp braga", "sc braga": "sp braga",
    "vitoria guimaraes": "guimaraes", "fc porto": "porto",
}
DAYS = ["Lun", "Mar", "Mer", "Gio", "Ven", "Sab", "Dom"]
NOW = datetime.now(timezone.utc)
remaining = [None]


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "schedina-lab"})
    with urllib.request.urlopen(req, timeout=30) as r:
        rem = r.headers.get("x-requests-remaining")
        if rem is not None:
            remaining[0] = float(rem)
        return r.read()


def api(path, **params):
    params["apiKey"] = KEY
    return json.loads(get(BASE + path + "?" + urllib.parse.urlencode(params)))


def low_credits():
    return remaining[0] is not None and remaining[0] < MIN_CREDITS


def parse_ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def in_window(ev):
    return NOW < parse_ts(ev["commence_time"]) <= NOW + timedelta(hours=WINDOW_H)


def label(ts):
    loc = ts.astimezone(ROME)
    return f"{DAYS[loc.weekday()]} {loc:%H:%M}"


def r2(x):
    return round(x, 2)


def r4(x):
    return round(x, 4)


def est(p):
    return max(1.05, r2(1 / (p * MARGIN)))


def collect(ev, market):
    """Quota media tra i bookmaker per ogni (esito, linea)."""
    acc = {}
    for b in ev.get("bookmakers", []):
        for m in b.get("markets", []):
            if m["key"] == market:
                for o in m["outcomes"]:
                    acc.setdefault((o["name"], o.get("point")), []).append(o["price"])
    return {k: sum(v) / len(v) for k, v in acc.items()}


# ---------- Calcio: modello di Poisson ----------
def norm(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower().strip()
    s = ALIAS.get(s, s)
    return " ".join(w for w in re.split(r"[^a-z0-9]+", s) if w and w not in STOP)


def find_team(name, names):
    n = norm(name)
    if n in names:
        return names[n]
    m = difflib.get_close_matches(n, list(names), n=1, cutoff=0.6)
    if m:
        return names[m[0]]
    for k in names:
        if k and (k in n or n in k):
            return names[k]
    print("squadra non riconosciuta:", name)
    return None


def has_events(key):
    """Evita di chiedere le quote a campionati senza partite. Si disattiva da solo se costa crediti."""
    global PRECHECK
    if not PRECHECK:
        return True
    before = remaining[0]
    try:
        events = api(f"/sports/{key}/events")
    except Exception as e:
        print("controllo preventivo non riuscito", key, e)
        return True
    if before is not None and remaining[0] is not None and remaining[0] < before:
        PRECHECK = False
        print("il controllo preventivo consuma crediti: disattivato")
    return any(in_window(e) for e in events)


def load_new(code):
    try:
        raw = get(f"https://www.football-data.co.uk/new/{code}.csv").decode("utf-8-sig", "replace")
    except Exception as e:
        print("CSV non disponibile", code, e)
        return []
    cutoff = NOW - timedelta(days=730)
    rows = []
    for r in csv.DictReader(io.StringIO(raw)):
        try:
            for fmt in ("%d/%m/%Y", "%d/%m/%y"):
                try:
                    d = datetime.strptime(r["Date"], fmt).replace(tzinfo=timezone.utc)
                    break
                except ValueError:
                    d = None
            if d and d >= cutoff:
                rows.append((r["Home"], r["Away"], int(r["HG"]), int(r["AG"])))
        except (KeyError, ValueError, TypeError):
            pass
    return rows


def load_rows(code):
    if code in NEW:
        return load_new(code)
    rows = []
    for s in SEASONS:
        try:
            raw = get(f"https://www.football-data.co.uk/mmz4281/{s}/{code}.csv").decode("utf-8-sig", "replace")
        except Exception as e:
            print("CSV non disponibile", s, code, e)
            continue
        for r in csv.DictReader(io.StringIO(raw)):
            try:
                rows.append((r["HomeTeam"], r["AwayTeam"], int(r["FTHG"]), int(r["FTAG"])))
            except (KeyError, ValueError, TypeError):
                pass
    return rows


def build_model(rows):
    if not rows:
        return None
    hg = sum(r[2] for r in rows) / len(rows)
    ag = sum(r[3] for r in rows) / len(rows)
    T = {}
    new = lambda: dict(hs=0, hc=0, hn=0, ws=0, wc=0, wn=0)
    for h, a, x, y in rows:
        t = T.setdefault(h, new()); t["hs"] += x; t["hc"] += y; t["hn"] += 1
        u = T.setdefault(a, new()); u["ws"] += y; u["wc"] += x; u["wn"] += 1
    K = 5  # partite "virtuali" a media di lega: evita stime estreme con pochi dati
    sh = lambda tot, n, avg: (tot + K * avg) / (n + K)

    def lam(h, a):
        th, ta = T[h], T[a]
        lh = hg * (sh(th["hs"], th["hn"], hg) / hg) * (sh(ta["wc"], ta["wn"], hg) / hg)
        la = ag * (sh(ta["ws"], ta["wn"], ag) / ag) * (sh(th["hc"], th["hn"], ag) / ag)
        return lh, la, min(th["hn"] + th["wn"], ta["hn"] + ta["wn"])

    return {"lam": lam, "names": {norm(n): n for n in T}}


def pm(k, l):
    return math.exp(-l) * l ** k / math.factorial(k)


def probs(lh, la):
    r = dict.fromkeys(("1", "X", "2", "o25", "u25"), 0.0)
    for i in range(10):
        for j in range(10):
            p = pm(i, lh) * pm(j, la)
            r["1" if i > j else "X" if i == j else "2"] += p
            r["o25" if i + j > 2 else "u25"] += p
    return r


def football_match(ev, lg, model):
    h, a = ev["home_team"], ev["away_team"]
    h2h, tot = collect(ev, "h2h"), collect(ev, "totals")
    o = {}
    for k, name in (("1", h), ("X", "Draw"), ("2", a)):
        if (name, None) in h2h:
            o[k] = r2(h2h[(name, None)])
    for k, name in (("o25", "Over"), ("u25", "Under")):
        if (name, 2.5) in tot:
            o[k] = r2(tot[(name, 2.5)])
    p, low = {}, True
    if model and o:
        mh, ma = find_team(h, model["names"]), find_team(a, model["names"])
        if mh and ma:
            lh, la, games = model["lam"](mh, ma)
            pr = probs(lh, la)
            p = {k: pr[k] for k in o}
            low = games < 8
    if not p:  # senza statistiche: probabilità implicite nelle quote 1X2
        inv = {k: 1 / o[k] for k in ("1", "X", "2") if k in o}
        if not inv:
            return None
        s = sum(inv.values())
        p = {k: v / s for k, v in inv.items()}
        o = {k: o[k] for k in p}
    ts = parse_ts(ev["commence_time"])
    return {"lg": lg, "h": h, "a": a, "t": label(ts), "ts": ts.isoformat(),
            "p": {k: r4(v) for k, v in p.items()}, "o": o, "low": low}


def football(active):
    out = []
    for key, (lg, code) in FOOTBALL.items():
        if key not in active:
            print("non attivo o chiave errata:", key)
            continue
        if low_credits():
            print("crediti quasi finiti, mi fermo")
            break
        if not has_events(key):
            continue
        try:
            events = api(f"/sports/{key}/odds", regions=REGIONS, markets=FOOTBALL_MARKETS, oddsFormat="decimal")
        except Exception as e:
            print("errore", key, e)
            continue
        events = [e for e in events if in_window(e)]
        if not events:
            continue
        model = build_model(load_rows(code))
        out += [m for m in (football_match(e, lg, model) for e in events) if m]
    return out


# ---------- Coppe europee: probabilità dal consenso dei bookmaker ----------
def consensus_match(ev, lg):
    h, a = ev["home_team"], ev["away_team"]
    h2h, tot = collect(ev, "h2h"), collect(ev, "totals")
    trio = (("1", h), ("X", "Draw"), ("2", a))
    if not all((n, None) in h2h for _, n in trio):
        return None
    inv = {k: 1 / h2h[(n, None)] for k, n in trio}
    s = sum(inv.values())
    o = {k: r2(h2h[(n, None)]) for k, n in trio}
    p = {k: r4(inv[k] / s) for k, _ in trio}
    if ("Over", 2.5) in tot and ("Under", 2.5) in tot:
        oo, ou = tot[("Over", 2.5)], tot[("Under", 2.5)]
        t = 1 / oo + 1 / ou
        o.update({"o25": r2(oo), "u25": r2(ou)})
        p.update({"o25": r4(1 / oo / t), "u25": r4(1 / ou / t)})
    ts = parse_ts(ev["commence_time"])
    return {"lg": lg, "h": h, "a": a, "t": label(ts), "ts": ts.isoformat(), "p": p, "o": o, "low": False}


def europe(active):
    out = []
    for key, lg in EUROPE.items():
        if key not in active:
            print("non attivo o chiave errata:", key)
            continue
        if low_credits():
            print("crediti quasi finiti, mi fermo")
            break
        if not has_events(key):
            continue
        try:
            events = api(f"/sports/{key}/odds", regions=REGIONS, markets=FOOTBALL_MARKETS, oddsFormat="decimal")
        except Exception as e:
            print("errore", key, e)
            continue
        out += [m for m in (consensus_match(e, lg) for e in events if in_window(e)) if m]
    return out


# ---------- Tennis: probabilità dal consenso dei bookmaker ----------
def match_win(s, b):
    return s ** 3 * (1 + 3 * (1 - s) + 6 * (1 - s) ** 2) if b == 5 else s ** 2 * (3 - 2 * s)


def set_prob(p, b):
    lo, hi = 0.0, 1.0
    for _ in range(40):
        m = (lo + hi) / 2
        if match_win(m, b) < p:
            lo = m
        else:
            hi = m
    return (lo + hi) / 2


def at_least_one(q, b):
    return 1 - (1 - q) ** (3 if b == 5 else 2)


def tennis(sports):
    out = []
    for sp in sports:
        key = sp["key"]
        lg = next((v for k, v in TENNIS.items() if key.startswith(k)), None)
        if not lg or not sp.get("active"):
            continue
        if low_credits():
            print("crediti quasi finiti, mi fermo")
            break
        if not has_events(key):
            continue
        b = 5 if lg == "ATP" and any(g in sp.get("title", "").lower() for g in GRAND_SLAMS) else 3
        try:
            events = api(f"/sports/{key}/odds", regions=REGIONS, markets="h2h", oddsFormat="decimal")
        except Exception as e:
            print("errore", key, e)
            continue
        for ev in events:
            if not in_window(ev):
                continue
            h, a = ev["home_team"], ev["away_team"]
            od = collect(ev, "h2h")
            if (h, None) not in od or (a, None) not in od:
                continue
            o1, o2 = od[(h, None)], od[(a, None)]
            p1 = (1 / o1) / (1 / o1 + 1 / o2)
            q = set_prob(p1, b)
            s1, s2 = at_least_one(q, b), at_least_one(1 - q, b)
            ts = parse_ts(ev["commence_time"])
            out.append({"lg": lg, "h": h, "a": a, "t": label(ts), "ts": ts.isoformat(),
                        "p": {"1": r4(p1), "2": r4(1 - p1), "s1": r4(s1), "s2": r4(s2)},
                        "o": {"1": r2(o1), "2": r2(o2), "s1": est(s1), "s2": est(s2)},
                        "lb": {"1": h, "2": a, "s1": h + " ≥1 set (quota stimata)", "s2": a + " ≥1 set (quota stimata)"},
                        "low": False})
    return out


# ---------- Basket: vincente e punti totali dal consenso ----------
def basket(active):
    out = []
    for key, lg in BASKET.items():
        if key not in active:
            print("non attivo o chiave errata:", key)
            continue
        if low_credits():
            print("crediti quasi finiti, mi fermo")
            break
        if not has_events(key):
            continue
        try:
            events = api(f"/sports/{key}/odds", regions=REGIONS, markets="h2h,totals", oddsFormat="decimal")
        except Exception as e:
            print("errore", key, e)
            continue
        for ev in events:
            if not in_window(ev):
                continue
            h, a = ev["home_team"], ev["away_team"]
            od = collect(ev, "h2h")
            if (h, None) not in od or (a, None) not in od:
                continue
            o1, o2 = od[(h, None)], od[(a, None)]
            p1 = (1 / o1) / (1 / o1 + 1 / o2)
            o, p, lb = {"1": r2(o1), "2": r2(o2)}, {"1": r4(p1), "2": r4(1 - p1)}, {"1": h, "2": a}
            lines = Counter(pt for b in ev.get("bookmakers", []) for m in b.get("markets", [])
                            if m["key"] == "totals" for x in m["outcomes"] if x["name"] == "Over" for pt in [x.get("point")])
            if lines:
                L = lines.most_common(1)[0][0]
                tot = collect(ev, "totals")
                if ("Over", L) in tot and ("Under", L) in tot:
                    oo, ou = tot[("Over", L)], tot[("Under", L)]
                    po = (1 / oo) / (1 / oo + 1 / ou)
                    txt = str(L).replace(".", ",")
                    o.update({"o2": r2(oo), "u2": r2(ou)})
                    p.update({"o2": r4(po), "u2": r4(1 - po)})
                    lb.update({"o2": "Over " + txt, "u2": "Under " + txt})
            ts = parse_ts(ev["commence_time"])
            out.append({"lg": lg, "h": h, "a": a, "t": label(ts), "ts": ts.isoformat(),
                        "p": p, "o": o, "lb": lb, "low": False})
    return out


def main():
    if not KEY:
        sys.exit("Manca la variabile ODDS_API_KEY")
    sports = api("/sports")  # elenco sport: secondo la documentazione non consuma crediti
    active = {s["key"] for s in sports if s.get("active")}
    matches = football(active) + europe(active) + tennis(sports) + basket(active)
    matches.sort(key=lambda m: m["ts"])
    data = {"updated": datetime.now(ROME).strftime("%d/%m %H:%M"), "matches": matches}
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    print(len(matches), "partite; crediti residui:", remaining[0])


if __name__ == "__main__":
    main()
