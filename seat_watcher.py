#!/usr/bin/env python3
"""
ASU seat watcher (cloud version, runs on GitHub Actions)
BIO 202 lab, Spring 2027, sections 27368 and 29383.

Each GitHub run checks once a minute for about 4.5 minutes, and a new run
starts every 5 minutes, so you get roughly minute-by-minute checks 24/7.
Alerts go to every device subscribed to your ntfy topic (iPhone, Android, Mac).
"""

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
SUBJECT = "BIO"
CATALOG_NUMBER = "202"
TERM = "2271"  # ASU code for Spring 2027
SECTIONS = {"27368": "Th 4:30-7:15 PM", "29383": "Th 1:15-4:00 PM"}
CHECK_EVERY_SECONDS = 60
RUN_FOR_SECONDS = 270  # ~4.5 minutes per GitHub run

API = "https://eadvs-cscc-catalog-api.apps.asu.edu/catalog-microservices/api/v1/search/classes"
CLASS_SEARCH_LINK = (
    "https://catalog.apps.asu.edu/catalog/classes/classlist?"
    f"campusOrOnlineSelection=A&honors=F&keywords={SUBJECT}%20{CATALOG_NUMBER}"
    f"&promod=F&searchType=all&term={TERM}"
)
PHOENIX = timezone(timedelta(hours=-7))


def log(msg):
    print(f"[{datetime.now(PHOENIX):%m/%d %I:%M:%S %p}] {msg}", flush=True)


def notify(title, message, priority="urgent"):
    req = urllib.request.Request(
        f"https://ntfy.sh/{NTFY_TOPIC}",
        data=message.encode("utf-8"),
        headers={"Title": title, "Priority": priority,
                 "Tags": "rotating_light", "Click": CLASS_SEARCH_LINK},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=15).read()
    except Exception as e:
        log(f"Could not send notification: {e}")


def fetch_classes():
    params = {
        "refine": "Y", "campusOrOnlineSelection": "A", "honors": "F",
        "promod": "F", "searchType": "all", "subject": SUBJECT,
        "catalogNbr": CATALOG_NUMBER, "term": TERM,
    }
    req = urllib.request.Request(
        API + "?" + urllib.parse.urlencode(params),
        headers={
            "Authorization": "Bearer null",
            "Accept": "application/json",
            "Origin": "https://catalog.apps.asu.edu",
            "Referer": "https://catalog.apps.asu.edu/",
            "User-Agent": "Mozilla/5.0 seat-watcher",
        },
    )
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


def check_once():
    data = fetch_classes()
    found = {}
    for item in data.get("classes", []):
        clas = item.get("CLAS", item)
        nbr = str(clas.get("CLASSNBR", "")).strip()
        if nbr in SECTIONS:
            seat = item.get("seatInfo") or {}
            found[nbr] = (_num(seat.get("ENRL_TOT"), clas.get("ENRLTOT")),
                          _num(seat.get("ENRL_CAP"), clas.get("ENRLCAP")))

    open_sections = []
    for nbr, label in SECTIONS.items():
        if nbr not in found or None in found[nbr]:
            log(f"{nbr}: couldn't read seat numbers")
            continue
        tot, cap = found[nbr]
        open_seats = cap - tot
        log(f"{nbr} ({label}): {max(open_seats, 0)} of {cap} open")
        if open_seats > 0:
            open_sections.append((nbr, label, open_seats))
    return found, open_sections


def main():
    if not NTFY_TOPIC:
        sys.exit("NTFY_TOPIC secret is missing. Add it in the repo's Settings > Secrets.")

    if os.environ.get("TEST_MODE") == "true":
        found, _ = check_once()
        log("ASU data looks good." if found else "Sections not found - send this log to Claude.")
        notify("Seat watcher test", "Cloud watcher works! You'll be alerted here.", priority="default")
        return

    alerted = set()
    start = time.time()
    failures = 0
    while True:
        try:
            _, open_sections = check_once()
            for nbr, label, n in open_sections:
                if nbr not in alerted:
                    notify(f"SEAT OPEN: BIO 202 #{nbr}",
                           f"{n} seat(s) open in {nbr} ({label}). Go add it NOW!")
                    log(f">>> ALERT SENT for {nbr}")
                    alerted.add(nbr)
        except Exception as e:
            failures += 1
            log(f"Error checking ASU: {e}")
        if time.time() - start + CHECK_EVERY_SECONDS > RUN_FOR_SECONDS:
            break
        time.sleep(CHECK_EVERY_SECONDS)

    if failures and failures == int(RUN_FOR_SECONDS / CHECK_EVERY_SECONDS) + 1:
        sys.exit(1)  # every check failed: mark the run red so GitHub emails you


if __name__ == "__main__":
    main()
