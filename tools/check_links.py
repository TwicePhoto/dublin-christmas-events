#!/usr/bin/env python3
"""Checks every event's link in events.json and flags the ones that have gone.

- A dead link gets "Listing removed." at the start of the event's notes and a
  "linkGone" date, so the app shows it.
- If the link is still dead GRACE_DAYS later, the event is dropped from the feed.
  (The Claude update task looks for relistings in between.)
- A link that works again has its flag cleared.
- Blocked, rate-limited or timed-out checks count as "unknown" and change nothing.
"""
import datetime as dt
import json
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

FEED = "events.json"
GRACE_DAYS = 4
MARK = "Listing removed."
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
GONE_TEXT = (
    "we can't find what you were looking for",   # Eventbrite
    "this event has ended",
    "page not found",
    "event not found",
    "no longer available",
)


def check(url: str) -> str:
    """Returns 'ok', 'gone' or 'unknown'."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en-IE,en"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            final = r.geturl()
            body = r.read(400_000).decode("utf-8", "ignore").lower()
    except urllib.error.HTTPError as e:
        if e.code in (404, 410):
            return "gone"
        if e.code == 400 and "eventbrite." in url:
            return "gone"
        return "unknown"
    except Exception:
        return "unknown"

    # Eventbrite sends removed events to a search or error page.
    if "eventbrite." in url and "/e/" in url and "/e/" not in final:
        return "gone"
    title = body.split("<title", 1)[-1][:300] if "<title" in body else ""
    if any(t in title for t in GONE_TEXT):
        return "gone"
    if "eventbrite." in url and "we can't find what you were looking for" in body:
        return "gone"
    return "ok"


def main() -> int:
    with open(FEED, encoding="utf-8") as f:
        feed = json.load(f)
    today = dt.date.today()
    events = feed["events"]

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda e: check(e["url"]), events))

    kept, marked, cleared, dropped = [], [], [], []
    for e, status in zip(events, results):
        notes = e.get("notes", "")
        if status == "gone":
            since = e.get("linkGone")
            if since and (today - dt.date.fromisoformat(since)).days >= GRACE_DAYS:
                dropped.append(e["title"])
                continue
            if not since:
                e["linkGone"] = today.isoformat()
                if not notes.startswith(MARK):
                    e["notes"] = f"{MARK} {notes}".strip()
                marked.append(e["title"])
        elif status == "ok" and e.get("linkGone"):
            e.pop("linkGone")
            if notes.startswith(MARK):
                e["notes"] = notes[len(MARK):].strip()
                if not e["notes"]:
                    e.pop("notes")
            cleared.append(e["title"])
        kept.append(e)

    unknown = sum(1 for r in results if r == "unknown")
    print(f"Checked {len(events)} links: {results.count('ok')} ok, {results.count('gone')} gone, {unknown} couldn't be checked.")
    for label, items in (("Marked as removed", marked), ("Working again", cleared), ("Dropped", dropped)):
        for t in items:
            print(f"{label}: {t}")

    if marked or cleared or dropped:
        feed["events"] = kept
        with open(FEED, "w", encoding="utf-8") as f:
            json.dump(feed, f, ensure_ascii=False, indent=1)
            f.write("\n")
        with open("link-check-summary.txt", "w") as f:
            parts = [f"{len(marked)} removed", f"{len(cleared)} back", f"{len(dropped)} dropped"]
            f.write("Link check: " + ", ".join(parts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
