"""Generate realistic, deterministic log files for compression testing.

Output files (in cfg.raw_dir/logs/):
  apache-access-100k.log    — Apache Common Log Format, 100,000 lines
  nginx-access-100k.log     — nginx combined log format, 100,000 lines
  json-events-100k.ndjson   — JSON event log, 100,000 lines
  syslog-100k.log           — syslog format, 100,000 lines

Realism properties (same across all formats):
  - 1000 distinct IPs with Zipfian distribution (hot IPs repeat frequently)
  - 200 distinct URL path templates with Zipfian distribution
  - Status code distribution: 70% 200, 10% 304, 8% 404, 5% 301, 4% 500, 3% other
  - 20 realistic user agents
  - Sequential timestamps: 2024-01-01 00:00:00 + 1 second per line
  - Response sizes realistic per status code

All generation uses random.Random with fixed per-file seeds for determinism.
Zipfian distribution is approximated by rank-weighted sampling (w ∝ 1/rank).
"""
from __future__ import annotations

import json
import math
import random
from datetime import datetime, timedelta
from pathlib import Path

from squishy.core.config import BuildConfig
from squishy.core.fs import write_bytes_atomic

# Fixed seeds per output file — one per format for independent generation
_SEEDS = {
    "apache":  0xA9ACE1099EED,   # arbitrary fixed constants
    "nginx":   0xA123456789,
    "json":    0xB987654321,
    "syslog":  0xC13579BDF,
}

_N_LINES = 100_000

_STATUS_DIST = [
    (200, 70),
    (304, 10),
    (404,  8),
    (301,  5),
    (500,  4),
    (400,  1),
    (403,  1),
    (502,  1),
]

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_2_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Android 14; Mobile; rv:121.0) Gecko/121.0 Firefox/121.0",
    "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)",
    "curl/8.5.0",
    "python-requests/2.31.0",
    "Go-http-client/1.1",
    "Apache-HttpClient/4.5.14 (Java/17.0.9)",
    "Wget/1.21.4",
    "Mozilla/5.0 (compatible; YandexBot/3.0; +http://yandex.com/bots)",
    "Mozilla/5.0 (compatible; DotBot/1.2; +https://opensiteexplorer.org/dotbot)",
    "PostmanRuntime/7.36.0",
    "okhttp/4.12.0",
    "Amazon CloudFront",
    "Slackbot-LinkExpanding 1.0 (+https://api.slack.com/robots)",
]

_URL_TEMPLATES = [
    "/",
    "/index.html",
    "/about",
    "/contact",
    "/products",
    "/products/{id}",
    "/api/v1/users",
    "/api/v1/users/{id}",
    "/api/v1/orders",
    "/api/v1/orders/{id}",
    "/api/v1/products",
    "/api/v1/search?q={term}",
    "/static/css/main.css",
    "/static/js/app.js",
    "/static/js/vendor.js",
    "/static/images/logo.png",
    "/static/images/hero.jpg",
    "/favicon.ico",
    "/robots.txt",
    "/sitemap.xml",
    "/blog",
    "/blog/{slug}",
    "/login",
    "/logout",
    "/signup",
    "/dashboard",
    "/settings",
    "/admin",
    "/admin/users",
    "/admin/orders",
    "/health",
    "/metrics",
    "/.well-known/acme-challenge/{token}",
    "/wp-admin",
    "/wp-login.php",
    "/xmlrpc.php",
    "/phpmyadmin",
    "/.env",
    "/config.php",
    "/api/v2/events",
    "/api/v2/events/{id}",
    "/api/v2/metrics",
    "/api/v2/status",
    "/feed.xml",
    "/rss",
    "/newsletter/subscribe",
    "/newsletter/unsubscribe/{token}",
    "/cdn-cgi/trace",
    "/cdn-cgi/l/email-protection",
    "/assets/main-{hash}.js",
] + [f"/category/{i}" for i in range(20)] + [f"/product/item-{i:04d}" for i in range(130)]


def _make_ips(n: int, rng: random.Random) -> list[str]:
    """Generate n distinct IP addresses."""
    ips = []
    seen: set[str] = set()
    while len(ips) < n:
        ip = f"{rng.randint(1,254)}.{rng.randint(0,255)}.{rng.randint(0,255)}.{rng.randint(1,254)}"
        if ip not in seen:
            seen.add(ip)
            ips.append(ip)
    return ips


def _zipf_weights(n: int) -> list[float]:
    """Return unnormalized Zipf weights for ranks 1..n (w_i = 1/i)."""
    return [1.0 / i for i in range(1, n + 1)]


def _weighted_choices(population: list, weights: list[float], k: int, rng: random.Random) -> list:
    return rng.choices(population, weights=weights, k=k)


def _status_choices(k: int, rng: random.Random) -> list[int]:
    statuses = [s for s, w in _STATUS_DIST for _ in range(w)]
    return [rng.choice(statuses) for _ in range(k)]


def _size_for_status(status: int, rng: random.Random) -> int:
    if status == 200:
        return rng.randint(512, 65536)
    elif status == 304:
        return 0
    elif status == 301:
        return rng.randint(150, 512)
    elif status == 404:
        return rng.randint(150, 2048)
    elif status == 500:
        return rng.randint(256, 4096)
    else:
        return rng.randint(128, 1024)


def _resolve_url(template: str, rng: random.Random) -> str:
    if "{id}" in template:
        return template.replace("{id}", str(rng.randint(1, 99999)))
    if "{slug}" in template:
        words = ["hello-world", "compression-tips", "benchmark-2024", "release-notes",
                 "getting-started", "faq", "tutorial", "deep-dive"]
        return template.replace("{slug}", rng.choice(words))
    if "{term}" in template:
        terms = ["compression", "zstd", "brotli", "deflate", "lz4", "benchmark", "test"]
        return template.replace("{term}", rng.choice(terms))
    if "{token}" in template or "{hash}" in template:
        import hashlib as _hl
        tok = _hl.sha256(str(rng.random()).encode()).hexdigest()[:16]
        return template.replace("{token}", tok).replace("{hash}", tok)
    return template


class _LogBuilder:
    """Shared state for generating a log file."""

    def __init__(self, seed: int) -> None:
        self.rng = random.Random(seed)
        self.ips = _make_ips(1000, self.rng)
        self.ip_weights = _zipf_weights(len(self.ips))
        self.urls = _URL_TEMPLATES[:200]
        self.url_weights = _zipf_weights(len(self.urls))
        self.base_ts = datetime(2024, 1, 1, 0, 0, 0)

    def ts(self, line: int) -> datetime:
        return self.base_ts + timedelta(seconds=line)

    def ip(self) -> str:
        return self.rng.choices(self.ips, weights=self.ip_weights, k=1)[0]

    def url(self) -> str:
        tmpl = self.rng.choices(self.urls, weights=self.url_weights, k=1)[0]
        return _resolve_url(tmpl, self.rng)

    def status(self) -> int:
        statuses = [s for s, w in _STATUS_DIST for _ in range(w)]
        return self.rng.choice(statuses)

    def ua(self) -> str:
        return self.rng.choice(_USER_AGENTS)

    def referer(self) -> str:
        refs = ["-", "https://www.google.com/", "https://example.com/",
                "https://news.ycombinator.com/", "-", "-", "-"]
        return self.rng.choice(refs)


def _gen_apache(n: int, seed: int) -> bytes:
    """Apache Common Log Format: host ident auth [date] "request" status size"""
    lb = _LogBuilder(seed)
    lines = []
    for i in range(n):
        ip = lb.ip()
        ts = lb.ts(i).strftime("%d/%b/%Y:%H:%M:%S +0000")
        method = lb.rng.choice(["GET", "GET", "GET", "POST", "HEAD"])
        url = lb.url()
        status = lb.status()
        size = _size_for_status(status, lb.rng)
        size_str = str(size) if size > 0 else "-"
        lines.append(f'{ip} - - [{ts}] "{method} {url} HTTP/1.1" {status} {size_str}\n')
    return "".join(lines).encode()


def _gen_nginx(n: int, seed: int) -> bytes:
    """nginx combined log format: adds referer and user-agent to common format."""
    lb = _LogBuilder(seed)
    lines = []
    for i in range(n):
        ip = lb.ip()
        ts = lb.ts(i).strftime("%d/%b/%Y:%H:%M:%S +0000")
        method = lb.rng.choice(["GET", "GET", "GET", "POST", "HEAD"])
        url = lb.url()
        status = lb.status()
        size = _size_for_status(status, lb.rng)
        size_str = str(size) if size > 0 else "0"
        referer = lb.referer()
        ua = lb.ua()
        lines.append(
            f'{ip} - - [{ts}] "{method} {url} HTTP/1.1" {status} {size_str}'
            f' "{referer}" "{ua}"\n'
        )
    return "".join(lines).encode()


def _gen_json_events(n: int, seed: int) -> bytes:
    """JSON event log: one JSON object per line."""
    lb = _LogBuilder(seed)
    event_types = ["page_view", "click", "form_submit", "api_call", "error",
                   "login", "logout", "purchase", "search", "download"]
    services = ["frontend", "api-gateway", "auth", "payments", "search", "cdn"]
    lines = []
    for i in range(n):
        ts = lb.ts(i).strftime("%Y-%m-%dT%H:%M:%SZ")
        status = lb.status()
        event = {
            "timestamp": ts,
            "event_type": lb.rng.choice(event_types),
            "source_ip": lb.ip(),
            "url": lb.url(),
            "status": status,
            "duration_ms": lb.rng.randint(1, 2000),
            "bytes": _size_for_status(status, lb.rng),
            "user_agent": lb.ua(),
            "service": lb.rng.choice(services),
            "request_id": f"req-{i:08x}",
            "trace_id": f"{lb.rng.randint(0, 0xFFFFFFFF):08x}-{lb.rng.randint(0, 0xFFFF):04x}",
        }
        lines.append(json.dumps(event, separators=(",", ":")) + "\n")
    return "".join(lines).encode()


def _gen_syslog(n: int, seed: int) -> bytes:
    """Syslog format: <priority>timestamp hostname process[pid]: message"""
    lb = _LogBuilder(seed)
    hostnames = [f"web-{i:02d}" for i in range(1, 11)] + \
                [f"app-{i:02d}" for i in range(1, 6)] + \
                [f"db-{i:02d}" for i in range(1, 4)]
    processes = ["sshd", "nginx", "kernel", "systemd", "cron", "postfix",
                 "sudo", "CRON", "su", "useradd"]
    facilities = [
        (1, 5, "kern.notice"),  # (facility, severity, name)
        (3, 6, "daemon.info"),
        (4, 5, "auth.notice"),
        (16, 6, "local0.info"),
    ]
    msgs = [
        "Connection from {ip} port {port}",
        "Accepted publickey for user from {ip} port {port} ssh2",
        "Failed password for invalid user admin from {ip} port {port} ssh2",
        "session opened for user deploy by (uid=0)",
        "session closed for user deploy",
        "pam_unix(sudo:session): session opened for user root by user(uid=1000)",
        "Started Service for {service}.",
        "Stopped Service for {service}.",
        "Reloading configuration.",
        "OOM killer invoked. Killed process {pid}.",
    ]
    lines = []
    for i in range(n):
        ts = lb.ts(i).strftime("%b %d %H:%M:%S").replace(" 0", "  ")
        hostname = lb.rng.choice(hostnames)
        process = lb.rng.choice(processes)
        pid = lb.rng.randint(100, 65535)
        fac, sev, _ = lb.rng.choice(facilities)
        priority = fac * 8 + sev
        tmpl = lb.rng.choice(msgs)
        msg = tmpl.format(
            ip=lb.ip(),
            port=lb.rng.randint(1024, 65535),
            service=lb.rng.choice(["nginx", "sshd", "cron", "postfix"]),
            pid=lb.rng.randint(1, 65535),
        )
        lines.append(f"<{priority}>{ts} {hostname} {process}[{pid}]: {msg}\n")
    return "".join(lines).encode()


def run(cfg: BuildConfig) -> int:
    """Generate all log files. Returns 0 on success, 1 on failure."""
    try:
        out = cfg.raw_dir / "logs"
        out.mkdir(parents=True, exist_ok=True)

        tasks = [
            ("apache-access-100k.log",  _gen_apache,      _SEEDS["apache"]),
            ("nginx-access-100k.log",   _gen_nginx,       _SEEDS["nginx"]),
            ("json-events-100k.ndjson", _gen_json_events, _SEEDS["json"]),
            ("syslog-100k.log",         _gen_syslog,      _SEEDS["syslog"]),
        ]

        for fname, gen_fn, seed in tasks:
            dest = out / fname
            if dest.exists():
                print(f"  skip {fname} (exists)")
                continue
            print(f"  generating {fname}...", flush=True)
            data = gen_fn(_N_LINES, seed)
            write_bytes_atomic(dest, data)
            print(f"  {fname} ({len(data):,} bytes)")

        print(f"  logs: all files written to {out}")
        return 0

    except Exception as exc:
        print(f"  ERROR in logs: {exc}")
        import traceback; traceback.print_exc()
        return 1
