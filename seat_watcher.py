#!/usr/bin/env python3
"""
ASU seat watcher (cloud version, runs on GitHub Actions) - Spring 2027

Two kinds of watches:
  mode "open"   -> urgent alert when a full section gets an open seat
  mode "change" -> alert whenever the open-seat count changes (e.g. 125 -> 124)
"""

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

# ---------------- WHAT TO WATCH (edit this list) ----------------
TERM = "2271"  # ASU code for Spring 2027
WATCHES = [
    {"subject": "BIO", "number": "202", "mode": "open",
     "sections": {"27368": "Th 4:30-7:15 PM", "29383": "Th 1:15-4:00 PM"}},
]
# ----------------------------------------------------------------

NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
CHECK_EVERY_SECONDS = 60
RUN_FOR_SECONDS = 3300
STATE_FILE = "state.json"
API = "https://eadvs-cscc-catalog-api.apps.asu.edu/catalog-microservices/api/v1/search/classes"
PHOENIX = timezone(timedelta(hours=-7))


def log(msg):
    print(f"[{datetime.now(PHOENIX):%m/%d %I:%M:%S %p}] {msg}", flush=True)


def search_link(w):
    return ("https://catalog.apps.asu.edu/catalog/classes/classlist?"
            f"campusOrOnlineSelection=A&honors=F&keywords={w['subject']}%20{w['number']}"
            f"&promod=F&searchType=all&term={TERM}")


def notify(title, message, priority="urgent", click=None, tags="rotating_light"):
    headers = {"Title": title, "Priority": priority, "Tags": tags}
    if click:
        headers["Click"] = click
    req = urllib.request.Request(f"https://ntfy.sh/{NTFY_TOPIC}",
                                 data=message.encode("utf-8"), headers=headers, method="POST")
    try:
        urllib.request.urlopen(req, timeout=15).read()
    except Exception as e:
        log(f"Could not send notification: {e}")


def fetch_classes(subject, number):
    params = {"refine": "Y", "campusOrOnlineSelection": "A", "honors": "F",
              "promod": "F", "searchType": "all", "subject": subject,
              "catalogNbr": number, "term": TERM}
    req = urllib.request.Request(
        API + "?" + urllib.parse.urlencode(params),
        headers={"Authorization": "Bearer null", "Accept": "application/json",
                 "Origin": "https://catalog.apps.asu.edu",
                 "Referer": "https://catalog.apps.asu.edu/",
                 "User-Agent": "Mozilla/5.0 seat-watcher"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def _num(*vals):
    for v in vals:
        try:
            if v is not None and str(v).strip() != "":
                return int(float(v))
        except ValueError:
            pass
    return None


def read_seats(w):
    data = fetch_classes(w["subject"], w["number"])
    out = {}
    for item in data.get("classes", []):
        clas = item.get("CLAS", item)
        nbr = str(clas.get("CLASSNBR", "")).strip()
        if nbr in w["sections"]:
            seat = item.get("seatInfo") or {}
            tot = _num(seat.get("ENRL_TOT"), clas.get("ENRLTOT"))
            cap = _num(seat.get("ENRL_CAP"), clas.get("ENRLCAP"))
            if tot is not None and cap is not None:
                out[nbr] = (max(cap - tot, 0), cap)
    return out


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except Exception as e:
        log(f"Could not save state: {e}")


def check_all(state, alerted_open):
    failures = 0
    for w in WATCHES:
        course = f"{w['subject']} {w['number']}"
        try:
            seats = read_seats(w)
        except Exception as e:
            log(f"{course}: error checking ASU: {e}")
            failures += 1
            continue
        for nbr, label in w["sections"].items():
            if nbr not in seats:
                log(f"{course} {nbr}: couldn't read seat numbers")
                continue
            open_n, cap = seats[nbr]
            log(f"{course} {nbr} ({label}): {open_n} of {cap} open")
            prev = state.get(nbr)

            if w["mode"] == "open":
                if open_n > 0:
                    notify(f"SEAT OPEN: {course} #{nbr}",
                           f"{open_n} seat(s) open in {nbr} ({label}). Go add it NOW!",
                           click=search_link(w))
                    log(f">>> ALERT SENT for {nbr}")
                    alerted_open.add(nbr)
                elif open_n == 0:
                    alerted_open.discard(nbr)

            elif w["mode"] == "change":
                if prev is not None and prev != open_n:
                    arrow = "up" if open_n > prev else "down"
                    notify(f"{course} #{nbr}: {prev} -> {open_n} open",
                           f"Open seats went {arrow} from {prev} to {open_n} (of {cap}).",
                           priority="default", click=search_link(w),
                           tags="chart_with_upwards_trend" if arrow == "up" else "chart_with_downwards_trend")
                    log(f">>> CHANGE ALERT for {nbr}: {prev} -> {open_n}")

            state[nbr] = open_n
    save_state(state)
    return failures


def main():
    if not NTFY_TOPIC:
        sys.exit("NTFY_TOPIC secret is missing. Add it in the repo's Settings > Secrets.")

    state = load_state()

    if os.environ.get("TEST_MODE") == "true":
        for w in WATCHES:
            course = f"{w['subject']} {w['number']}"
            try:
                seats = read_seats(w)
                for nbr, label in w["sections"].items():
                    if nbr in seats:
                        log(f"{course} {nbr} ({label}): {seats[nbr][0]} of {seats[nbr][1]} open")
                    else:
                        log(f"{course} {nbr}: couldn't read seat numbers")
            except Exception as e:
                log(f"{course}: error checking ASU: {e}")
        notify("Seat watcher test", "Cloud watcher works! You'll be alerted here.", priority="default")
        log("Test notification sent.")
        return

    alerted_open = set()
    start = time.time()
    total_checks = 0
    all_failed = 0
    while True:
        total_checks += 1
        if check_all(state, alerted_open) == len(WATCHES):
            all_failed += 1
        if time.time() - start + CHECK_EVERY_SECONDS > RUN_FOR_SECONDS:
            break
        time.sleep(CHECK_EVERY_SECONDS)

    if all_failed == total_checks:
        sys.exit(1)


if __name__ == "__main__":
    main()
