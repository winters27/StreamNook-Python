#!/usr/bin/env python3
# 7tv_trending_monthly.py — v4 GraphQL → trending emote URLs → text file with live progress
#
# Examples:
#   python 7tv_trending_monthly.py --out trending.txt --debug
#   python 7tv_trending_monthly.py --out trending.txt --status-file trending.status.json
#   python 7tv_trending_monthly.py --exact --stream
#   python 7tv_trending_monthly.py --follow 60
#
# Notes:
# - Queries https://api.7tv.app/v4/gql (override with --gql-url)
# - Filters animated=true, sorts TRENDING_MONTHLY, paginates page=1..pageCount
# - Writes incrementally after each page so your app can read as it grows

import json, sys, time, argparse, urllib.request, urllib.error, math, shutil, signal
from pathlib import Path
from typing import List, Optional, Set, Tuple

GQL_URL_DEFAULT = "https://api.7tv.app/v4/gql"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:144.0) Gecko/20100101 Firefox/144.0"

FORMAT_PREF: List[str] = ["webp", "avif", "gif"]
SCALE_PREF: List[str] = ["3x", "4x", "2x", "1x"]

GQL_QUERY = """
query EmoteSearch(
  $query: String,
  $tags: [String!],
  $sortBy: SortBy!,
  $filters: Filters,
  $page: Int,
  $perPage: Int!
) {
  emotes {
    search(
      query: $query
      tags: { tags: $tags, match: ANY }
      sort: { sortBy: $sortBy, order: DESCENDING }
      filters: $filters
      page: $page
      perPage: $perPage
    ) {
      items {
        id
        images { url mime scale frameCount }
      }
      totalCount
      pageCount
    }
  }
}
""".strip()

# -------------- HTTP helpers --------------

def _build_headers(bearer: Optional[str]) -> dict:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/graphql-response+json, application/graphql+json, application/json",
        "User-Agent": UA,
        "Origin": "https://7tv.app",
        "Referer": "https://7tv.app/",
    }
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    return headers

def _post_json(url: str, payload: dict, timeout: float, bearer: Optional[str]) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=_build_headers(bearer), method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
        return json.loads(body.decode("utf-8"))

# -------------- selection logic --------------

def _norm_scale(v) -> str:
    if v is None: return ""
    if isinstance(v, int): return f"{v}x"
    s = str(v).strip().lower()
    if s.endswith("x"): return s
    if s.isdigit(): return f"{s}x"
    return s

def _pick_best_url(images: List[dict]) -> Optional[str]:
    animated = [im for im in images if (im.get("frameCount") or 0) > 1]
    candidates = animated if animated else images
    ranked: List[Tuple[int,int,str]] = []
    for im in candidates:
        url = im.get("url")
        if not url: continue
        mime = (im.get("mime") or "").lower()
        fmt = mime.split("/")[-1] if "/" in mime else (url.rsplit(".",1)[-1].lower() if "." in url else "")
        scale = _norm_scale(im.get("scale"))
        f_rank = FORMAT_PREF.index(fmt) if fmt in FORMAT_PREF else 99
        s_rank = SCALE_PREF.index(scale) if scale in SCALE_PREF else 99
        ranked.append((f_rank, s_rank, url))
    if not ranked: return None
    ranked.sort(key=lambda t: (t[0], t[1]))
    return ranked[0][2]

# -------------- I/O --------------

def load_existing_lines(path: Path) -> Set[str]:
    if not path.exists():
        return set()
    try:
        return {ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()}
    except Exception:
        return set()

def append_lines(path: Path, lines: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for u in lines:
            f.write(u + "\n")

def write_status(status_path: Optional[Path], status: dict):
    if not status_path:
        return
    status_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = status_path.with_suffix(status_path.suffix + ".tmp")
    tmp.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(status_path)

# -------------- progress UI --------------

def _fmt_rate(done: int, elapsed: float) -> str:
    if elapsed <= 0: return "—/s"
    return f"{done/elapsed:.1f}/s"

def _progress_bar(frac: float, width: int) -> str:
    frac = max(0.0, min(1.0, frac))
    fill = int(frac * width)
    return "█" * fill + "░" * (width - fill)

def print_progress(tty: bool, page: int, page_count: int, total_urls: int, written: int, start_ts: float):
    elapsed = time.time() - start_ts
    cols = shutil.get_terminal_size(fallback=(80, 20)).columns
    bar_w = max(10, min(40, cols - 50))
    frac = (page-1) / page_count if page_count else 0.0
    bar = _progress_bar(frac, bar_w)
    msg = f"[{bar}] pages {page-1}/{page_count}  urls {total_urls}  +{written}  { _fmt_rate(total_urls, elapsed) }"
    if tty:
        sys.stdout.write("\r" + msg.ljust(cols))
        sys.stdout.flush()
    else:
        print(msg)

# -------------- core fetch --------------

def fetch_trending_urls_incremental(
    *,
    gql_url: str,
    timeout: float,
    per_page: int,
    exact_pattern: bool,
    bearer: Optional[str],
    stream: bool,
    status_path: Optional[Path],
    out_path: Path,
    tty: bool,
) -> None:
    start_ts = time.time()
    existing = load_existing_lines(out_path)
    written_total = 0
    urls_seen_total = 0
    page = 1
    page_count = None
    stopped = False

    try:
        while True:
            variables = {
                "filters": {"animated": True},
                "page": page,
                "perPage": per_page,
                "query": None,
                "sortBy": "TRENDING_MONTHLY",
                "tags": [],
            }
            payload = {"operationName": "EmoteSearch", "query": GQL_QUERY, "variables": variables}

            try:
                resp = _post_json(gql_url, payload, timeout=timeout, bearer=bearer)
            except urllib.error.HTTPError as e:
                print(f"\n[!] HTTP {e.code} on page {page}: {e.reason}", file=sys.stderr)
                break
            except KeyboardInterrupt:
                print("\n[!] Interrupted, finishing up…")
                stopped = True
                break
            except Exception as e:
                print(f"\n[!] Request error on page {page}: {e}", file=sys.stderr)
                break

            if resp.get("errors"):
                print(f"\n[!] GraphQL error: {resp['errors'][0].get('message')}", file=sys.stderr)
                break

            data = (resp.get("data") or {})
            search = ((data.get("emotes") or {}).get("search")) or {}
            items = search.get("items") or []

            if page_count is None:
                page_count = search.get("pageCount") or 1

            add_now: List[str] = []
            for it in items:
                if exact_pattern:
                    eid = it.get("id")
                    if eid:
                        url = f"https://cdn.7tv.app/emote/{eid}/3x.avif"
                    else:
                        continue
                else:
                    url = _pick_best_url(it.get("images") or [])
                    if not url:
                        continue
                urls_seen_total += 1
                if url not in existing:
                    add_now.append(url)
                    existing.add(url)
                if stream:
                    print(url)

            if add_now:
                append_lines(out_path, add_now)
                written_total += len(add_now)

            # live status + progress
            print_progress(tty, page + 1, page_count or 1, urls_seen_total, written_total, start_ts)
            write_status(status_path, {
                "page": page,
                "page_count": page_count,
                "urls_seen_total": urls_seen_total,
                "written_total": written_total,
                "out_file": str(out_path),
                "started_at": start_ts,
                "updated_at": time.time(),
            })

            if not items or (page_count and page >= page_count):
                break
            page += 1

    finally:
        # final line so the prompt isn't stuck mid-progress
        if sys.stdout and sys.stdout.isatty():
            sys.stdout.write("\n")
        elapsed = time.time() - start_ts
        print(f"[✓] pages {page- (0 if stopped else 0)}/{page_count or '?'}  "
              f"urls {urls_seen_total}  wrote +{written_total}  "
              f"in {elapsed:.1f}s  ({_fmt_rate(urls_seen_total, elapsed)})")
        write_status(status_path, {
            "done": True,
            "page": page,
            "page_count": page_count,
            "urls_seen_total": urls_seen_total,
            "written_total": written_total,
            "out_file": str(out_path),
            "started_at": start_ts,
            "finished_at": time.time(),
            "elapsed_sec": elapsed,
        })

# -------------- CLI --------------

def main():
    ap = argparse.ArgumentParser("7TV monthly trending (v4 GraphQL) → text file, with live progress.")
    ap.add_argument("--out", default="trending_emotes.txt", help="Output (one URL per line).")
    ap.add_argument("--per-page", type=int, default=72, help="Pagination size.")
    ap.add_argument("--timeout", type=float, default=12.0, help="HTTP timeout (s).")
    ap.add_argument("--follow", type=int, default=0, help="Re-run every N minutes (0 = once).")
    ap.add_argument("--exact", action="store_true", help="Write https://cdn.7tv.app/emote/<id>/3x.avif")
    ap.add_argument("--gql-url", default=GQL_URL_DEFAULT, help="GraphQL endpoint.")
    ap.add_argument("--bearer", default=None, help="Bearer token (optional).")
    ap.add_argument("--stream", action="store_true", help="Print each URL as it’s discovered.")
    ap.add_argument("--status-file", default=None, help="Write progress JSON here (your app can read this).")
    ap.add_argument("--no-tty", action="store_true", help="Disable dynamic progress bar (use plain prints).")
    args = ap.parse_args()

    out_path = Path(args.out)
    status_path = Path(args.status_file) if args.status_file else None
    tty = sys.stdout.isatty() and not args.no_tty

    def run_once():
        fetch_trending_urls_incremental(
            gql_url=args.gql_url,
            timeout=args.timeout,
            per_page=args.per_page,
            exact_pattern=bool(args.exact),
            bearer=args.bearer,
            stream=bool(args.stream),
            status_path=status_path,
            out_path=out_path,
            tty=tty,
        )

    if args.follow > 0:
        try:
            while True:
                run_once()
                time.sleep(args.follow * 60)
        except KeyboardInterrupt:
            print("\nStopping.")
    else:
        run_once()

if __name__ == "__main__":
    main()
