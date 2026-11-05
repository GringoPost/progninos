"""Copy today's Progniños report into one existing WordPress page.

Runs after progninos.py. Does nothing unless these four settings exist (GitHub: Settings > Secrets and
variables > Actions):

    WP_SITE          e.g. https://staging-9654-mygringopost.wpcomstaging.com  (test site first)
    WP_USER          the WordPress username that owns the application password
    WP_APP_PASSWORD  an Application Password for that user (never the normal login password)
    WP_PAGE_ID       the ID of the page to update (create it by hand first, as a draft)

It only replaces the page's content. It never creates, publishes, unpublishes or deletes anything: the page
keeps whatever status (draft, private, published) a person gave it in WordPress.

If any automatic quality check failed, the page is left as it was, unless WP_PUBLISH_WITH_WARNINGS=1.
"""
import base64
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOCAL = dt.timezone(dt.timedelta(hours=-5))
UA = "progninos/1.0 (+https://gringopost.com)"


def main():
    cfg = {k: os.environ.get(k, "").strip() for k in ("WP_SITE", "WP_USER", "WP_APP_PASSWORD", "WP_PAGE_ID")}
    missing = [k for k, v in cfg.items() if not v]
    if missing:
        print(f"WordPress publishing is not set up (missing: {', '.join(missing)}). Skipping.")
        return 0

    today = dt.datetime.now(dt.timezone.utc).astimezone(LOCAL).date().isoformat()
    folder = HERE / "output" / today
    site_html, checks_file = folder / "site.html", folder / "checks.json"
    if not site_html.exists():
        print(f"No site.html for {today}; the page was not changed.")
        return 1

    failed = [c["check"] for c in json.loads(checks_file.read_text(encoding="utf-8"))["checks"]
              if c["status"] == "fail"]
    if failed and os.environ.get("WP_PUBLISH_WITH_WARNINGS") != "1":
        print(f"Quality checks failed ({', '.join(failed)}); the page keeps yesterday's report.")
        return 0

    site = cfg["WP_SITE"].rstrip("/")
    if not site.startswith("https://"):
        print("WP_SITE must start with https://")
        return 1
    url = f"{site}/wp-json/wp/v2/pages/{int(cfg['WP_PAGE_ID'])}"
    token = base64.b64encode(f"{cfg['WP_USER']}:{cfg['WP_APP_PASSWORD'].replace(' ', '')}".encode()).decode()
    headers = {"Authorization": f"Basic {token}", "User-Agent": UA, "Content-Type": "application/json"}

    body = json.dumps({"content": site_html.read_text(encoding="utf-8")}).encode()
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            page = json.loads(r.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        print(f"WordPress refused the update: HTTP {exc.code}. {detail}")
        return 1

    rendered = page.get("content", {}).get("rendered", "")
    print(f"Updated page {page.get('id')} ({page.get('status')}): {page.get('link')}")
    if "<svg" not in rendered or "<style" not in rendered:
        print("Warning: WordPress removed the charts or styles. The user in WP_USER needs to be an "
              "Administrator so the Custom HTML block is kept as is.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
