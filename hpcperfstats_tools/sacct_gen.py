"""Run sacct for a date range and send pipe-delimited output to the HPCPerfStats API.

No longer writes files; each day's output is POSTed to the API sacct/ingest/
endpoint, which uses sync_acct logic to store data in the database.

Requires API base URL in the INI file pointed to by HPCPERFSTATS_TOOLS_INI ([API] base_url). For authentication,
use --api-key or a key cached in ~/.hpcperfstats-api (same scheme as jobstats_cli).
"""
import argparse
import os
import subprocess
import sys
from datetime import datetime, timedelta
from typing import Optional

import requests
from dateutil.parser import parse

from .api_key_cache import (
    API_KEY_CACHE,
    api_key_help_url,
    load_cached_api_key,
    save_cached_api_key,
)
from .config import get_api_base_url


def _daterange(start_date: datetime, end_date: datetime, inclusive_end: bool = False):
    """Yield each date from start_date through end_date, one day at a time."""
    days = int((end_date - start_date).days)
    if inclusive_end:
        days += 1
    for n in range(max(0, days)):
        yield start_date + timedelta(n)


SACCT_FIELDS = (
    "jobid,jobidraw,cluster,partition,qos,account,group,gid,user,uid,"
    "submit,eligible,start,end,elapsed,exitcode,state,nnodes,ncpus,reqcpus,"
    "reqmem,reqtres,reqtres,timelimit,nodelist,jobname"
)


def run_sacct_for_date(single_date):
    """Run sacct for a single day; return (date_str, stdout_bytes) or (date_str, None) on failure."""
    start_str = single_date.strftime("%Y-%m-%d")
    end_date = single_date + timedelta(1)
    end_str = end_date.strftime("%Y-%m-%d")
    cmd = [
        "/bin/sacct",
        "-a",
        "-s", "CANCELLED,COMPLETED,FAILED,NODE_FAIL,PREEMPTED,TIMEOUT,OUT_OF_MEMORY",
        "-P", "-X",
        "-S", start_str,
        "-E", end_str,
        "-o", SACCT_FIELDS,
    ]
    env = os.environ.copy()
    env["TZ"] = "UTC"
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=False,
        timeout=3600,
        env=env,
    )
    if result.returncode != 0:
        return start_str, None
    return start_str, result.stdout


def send_to_api(base_url, api_key, date_str, body):
    """POST sacct output to the ingest endpoint. Return (success, message).

    Some deployments may issue an HTTP redirect (for example, HTTP→HTTPS or
    path normalization). The Python requests library may convert a POST into a
    GET when following a 301/302 redirect, which would cause Django to return
    "405 Method Not Allowed" on the ingest view (which only allows POST).

    To avoid this, we first send the request with redirects disabled and, if
    we receive a redirect status with a Location header, we re‑POST once to
    the redirected URL while preserving the HTTP method and body.
    """
    url = f"{base_url.rstrip('/')}/sacct/ingest/?date={date_str}"
    headers = {"Content-Type": "text/plain; charset=utf-8"}
    if api_key:
        headers["Authorization"] = f"Api-Key {api_key}"

    try:
        # First request: do not auto-follow redirects, so we can preserve POST.
        r = requests.post(url, data=body, headers=headers, timeout=300, allow_redirects=False)

        # Handle a single explicit redirect hop while keeping POST.
        if r.is_redirect or r.status_code in (301, 302, 303, 307, 308):
            redirect_url = r.headers.get("Location")
            if redirect_url:
                # Absolute or relative Location are both supported by requests.
                r = requests.post(redirect_url, data=body, headers=headers, timeout=300)

        r.raise_for_status()
        data = r.json()
        return True, data.get("inserted", 0)
    except requests.RequestException as e:
        return False, str(e)
    except ValueError as e:
        return False, f"Invalid JSON: {e}"


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description="Run sacct for a date range and POST output to the HPCPerfStats API.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Environment variables:
  HPCPERFSTATS_TOOLS_INI  Path to INI file with [API] base_url. Required for
                         this tool; the API base URL is read from this file only.

Files:
  %s  Cached API keys per API base URL. Written when you pass --api-key.
""" % API_KEY_CACHE,
    )
    parser.add_argument(
        "start_date",
        nargs="?",
        default=None,
        help="Start date (YYYY-MM-DD or parseable). Default: today.",
    )
    parser.add_argument(
        "end_date",
        nargs="?",
        default=None,
        help="End date (exclusive). Default: start_date + 1 day.",
    )
    parser.add_argument(
        "--api-key",
        help="API key for authenticating to the HPCPerfStats REST API. "
        "If omitted, a cached key in %s is used when present." % API_KEY_CACHE,
    )
    args = parser.parse_args(argv)

    try:
        start_date = parse(args.start_date) if args.start_date else datetime.now()
    except Exception:
        start_date = datetime.now()

    try:
        end_date = parse(args.end_date) if args.end_date else start_date + timedelta(1)
    except Exception:
        end_date = start_date + timedelta(1)

    base_url = get_api_base_url(default=None) or None
    if not base_url:
        print(
            "Error: API base URL not set. Set [API] base_url in "
            "HPCPERFSTATS_TOOLS_INI to point to your INI file with [API] base_url set.",
            file=sys.stderr,
        )
        sys.exit(1)

    api_key = args.api_key
    if api_key:
        save_cached_api_key(base_url, api_key)
    else:
        api_key = load_cached_api_key(base_url)
    if not api_key:
        help_url = api_key_help_url(base_url)
        print(
            "No API key found. Create one at this browsable page:\n  %s\n"
            "Then run this command again with --api-key (it will be cached for future use)."
            % help_url,
            file=sys.stderr,
        )
        sys.exit(1)

    for single_date in _daterange(start_date, end_date):
        date_str, output = run_sacct_for_date(single_date)
        if output is None:
            print(f"Warning: sacct failed for {date_str}", file=sys.stderr)
            continue
        body = output.decode("utf-8", errors="replace")
        ok, msg = send_to_api(base_url, api_key, date_str, body)
        if ok:
            print(f"{date_str}: ingested {msg} new job(s)")
        else:
            print(f"{date_str}: ingest failed: {msg}", file=sys.stderr)


if __name__ == "__main__":
    main()
