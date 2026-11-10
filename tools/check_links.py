#!/usr/bin/env python3
"""Checks every event's link in events.json and flags the ones that have gone.

- A dead link gets "Listing not found, check before going." at the start of the
  event's notes and a "linkGone" date, so the app can show it. Nothing is ever
  removed from the feed; Paul decides.
- The Claude update task looks for relistings of flagged events.
- A link that works again has its flag cleared.
- Blocked, rate-limited or timed-out checks count as "unknown" and change nothing.
"""
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

FEED = "events.json"
MARK = "Listing not found, check before going."
OLD_MARKS = ("Listing removed.",)
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
GONE_TEXT = (
    "something went wrong",   # Eventbrite's missing-event page
    "this event has ended",
    "page not found",
    "event not found",
    "no longer available",
)


DETAILS = {}


def check(url: str) -> str:
    status = _check(url)
    return status


def _check(url: str) -> str:
    """Returns 'ok', 'gone' or 'unknown'."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en-IE,en"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            final = r.geturl()
            body = r.read(400_000).decode("utf-8", "ignore").lower()
    except urllib.error.HTTPError as e:
        DETAILS[url] = f"HTTP {e.code} final={e.geturl()}"
        # Only a definite "not found" counts. Blocks (403/405), rate limits and
        # server errors say nothing about whether the event still exists.
        if e.code in (404, 410):
            return "gone"
        return "unknown"
    except Exception as e:
        DETAILS[url] = f"error {type(e).__name__}: {e}"
        return "unknown"
    DETAILS[url] = f"200 final={final} title={(body.split('<title', 1)[-1][:120] if '<title' in body else '').strip()!r}"

    # Eventbrite sends removed events to a search or error page.
    if "eventbrite." in url and "/e/" in url and "/e/" not in final:
        return "gone"
    # Look at the page title only: the page's code can contain error wording
    # (Eventbrite's does) even when the event is live.
    title = body.split("<title", 1)[-1].split("</title", 1)[0][:300] if "<title" in body else ""
    if any(t in title for t in GONE_TEXT):
        return "gone"
    return "ok"


def main() -> int:
    with open(FEED, encoding="utf-8") as f:
        feed = json.load(f)
    today = dt.date.today()
    events = feed["events"]

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda e: check(e["url"]), events))

    def strip_marks(notes: str) -> str:
        for m in (MARK, *OLD_MARKS):
            if notes.startswith(m):
                return notes[len(m):].strip()
        return notes

    kept, marked, cleared, dropped = [], [], [], []
    for e, status in zip(events, results):
        notes = e.get("notes", "")
        if status == "gone":
            if not e.get("linkGone"):
                e["linkGone"] = today.isoformat()
                marked.append(e["title"])
            rest = strip_marks(notes)
            e["notes"] = f"{MARK} {rest}".strip()
        elif status == "ok" and e.get("linkGone"):
            e.pop("linkGone")
            e["notes"] = strip_marks(notes)
            if not e["notes"]:
                e.pop("notes")
            cleared.append(e["title"])
        kept.append(e)

    if os.environ.get("REPORT_ONLY"):
        for e, status in zip(events, results):
            if status != "ok":
                print(f"[{status}] {e['title']} | {e['url']} | {DETAILS.get(e['url'])}")
        oks = [e for e, s in zip(events, results) if s == "ok" and "eventbrite." in e["url"]][:3]
        for e in oks:
            print(f"[ok sample] {e['url']} | {DETAILS.get(e['url'])}")
        print(f"Report only: {results.count('ok')} ok, {results.count('gone')} gone, {results.count('unknown')} unknown")
        return 0

    # Safety: if lots of links look dead at once, the check itself is probably
    # being blocked or has broken, so change nothing and fail loudly.
    if results.count("gone") > max(5, len(events) // 10):
        print(f"Refusing to change anything: {results.count('gone')} of {len(events)} links look dead at once.")
        for e, status in zip(events, results):
            if status == "gone":
                print(f"  {e['title']} | {DETAILS.get(e['url'])}")
        return 1

    unknown = sum(1 for r in results if r == "unknown")
    print(f"Checked {len(events)} links: {results.count('ok')} ok, {results.count('gone')} gone, {unknown} couldn't be checked.")
    for label, items in (("Not found", marked), ("Working again", cleared)):
        for t in items:
            print(f"{label}: {t}")

    changed = json.dumps(kept, sort_keys=True) != json.dumps(json.load(open(FEED, encoding="utf-8"))["events"], sort_keys=True)
    if changed:
        feed["events"] = kept
        with open(FEED, "w", encoding="utf-8") as f:
            json.dump(feed, f, ensure_ascii=False, indent=1)
            f.write("\n")
        with open("link-check-summary.txt", "w") as f:
            parts = [f"{len(marked)} not found", f"{len(cleared)} working again"]
            f.write("Link check: " + ", ".join(parts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
