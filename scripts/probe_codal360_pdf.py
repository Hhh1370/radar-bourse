#!/usr/bin/env python3
"""Probe whether Codal360's public PDF links can be downloaded from GitHub Actions.

Only a few URLs are tested. The script does not treat a blocked PDF host as a
missing disclosure and never calls a paid API.
"""
from __future__ import annotations

import html
import io
import json
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "codal360_pdf_probe.json"
CHANNEL_URL = "https://t.me/s/Codal360_ir"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
CONNECT_TIMEOUT = 6
DOWNLOAD_TIMEOUT = 10
MAX_PDF_BYTES = 8 * 1024 * 1024
MAX_PDFS = 3


def clean(text):
    text = html.unescape(str(text or ""))
    text = text.replace("\u200c", " ").replace("\u200f", "").replace("\u200e", "")
    return re.sub(r"\s+", " ", text).strip()


def norm(text):
    value = clean(text).lower()
    return re.sub(r"[\s‌\-_]+", "", value)


class TelegramPdfLinks(HTMLParser):
    VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input",
                 "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.post_depth = 0
        self.in_post = False
        self.current_anchor = None
        self.links = []
        self.post_id = ""
        self.current_post_id = ""

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        cls = attrs.get("class", "")
        if tag.lower() == "div" and "tgme_widget_message_wrap" in cls and not self.in_post:
            self.in_post = True
            self.post_depth = 1
            data_post = attrs.get("data-post", "")
            self.current_post_id = data_post.rsplit("/", 1)[-1] if "/" in data_post else ""
            return
        if not self.in_post:
            return
        if tag.lower() == "div":
            self.post_depth += 1
        if tag.lower() == "a" and attrs.get("href"):
            self.current_anchor = {
                "url": urllib.parse.urljoin(CHANNEL_URL, html.unescape(attrs["href"])),
                "text_parts": [],
                "post_id": self.current_post_id,
            }

    def handle_data(self, data):
        if self.in_post and self.current_anchor is not None and clean(data):
            self.current_anchor["text_parts"].append(clean(data))

    def handle_endtag(self, tag):
        if not self.in_post:
            return
        if tag.lower() == "a" and self.current_anchor is not None:
            self.current_anchor["title"] = clean(" ".join(self.current_anchor.pop("text_parts", [])))
            self.links.append(self.current_anchor)
            self.current_anchor = None
        if tag.lower() == "div":
            self.post_depth -= 1
            if self.post_depth <= 0:
                self.in_post = False
                self.post_depth = 0
                self.current_anchor = None


def is_pdf_candidate(item):
    url = item.get("url", "")
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path.lower()
    title = norm(item.get("title", ""))
    query = urllib.parse.parse_qs(parsed.query)
    is_codal = host == "codal.ir" or host.endswith(".codal.ir")
    is_download_endpoint = path.endswith("/downloadfile.aspx") or path.endswith("downloadfile.aspx")
    is_excel = host == "excel.codal.ir" or host.endswith(".excel.codal.ir") or "excel" in title
    has_pdf_label = "pdf" in title or "دانلودفایلpdf" in title
    has_pdf_type = any(v == "1005" for v in query.get("ft", []))
    return is_codal and is_download_endpoint and not is_excel and (has_pdf_label or has_pdf_type)


def fetch_page(url):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            "Accept-Language": "fa-IR,fa;q=0.9,en;q=0.7",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=18) as resp:
            body = resp.read(8 * 1024 * 1024 + 1)
            return {
                "http_status": resp.status,
                "content_type": resp.headers.get("Content-Type", ""),
                "body": body[:8 * 1024 * 1024],
                "body_bytes": len(body),
                "error": "",
            }
    except Exception as exc:
        return {"http_status": 0, "content_type": "", "body": b"", "body_bytes": 0,
                "error": f"{type(exc).__name__}: {exc}"[:300]}


def download_pdf(url):
    with tempfile.NamedTemporaryFile(prefix="codal360-", suffix=".pdf") as target:
        cmd = [
            "curl", "--location", "--http1.1",
            "--connect-timeout", str(CONNECT_TIMEOUT),
            "--max-time", str(DOWNLOAD_TIMEOUT),
            "--max-filesize", str(MAX_PDF_BYTES),
            "--retry", "0",
            "--user-agent", USER_AGENT,
            "--header", "Accept: application/pdf,*/*;q=0.8",
            "--silent", "--show-error", "--output", target.name,
            "--write-out", "%{http_code}|%{content_type}|%{url_effective}", url,
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=DOWNLOAD_TIMEOUT + 3, check=False)
            fields = (proc.stdout or "").strip().split("|", 2)
            status = fields[0] if fields and fields[0] else "ERROR"
            try:
                status = int(status)
            except (TypeError, ValueError):
                pass
            content_type = fields[1] if len(fields) > 1 else ""
            effective_url = fields[2] if len(fields) > 2 else url
            target.flush()
            size = Path(target.name).stat().st_size
            too_large = size > MAX_PDF_BYTES or proc.returncode == 63
            with open(target.name, "rb") as f:
                body = f.read(MAX_PDF_BYTES + 1)
            error = clean(proc.stderr)
            if too_large:
                error = f"Download exceeded the {MAX_PDF_BYTES}-byte limit."
            elif proc.returncode != 0 and not error:
                error = f"curl exited with code {proc.returncode}"
            return {
                "http_status": status,
                "content_type": content_type,
                "effective_url": effective_url or url,
                "body": body,
                "body_bytes": len(body),
                "too_large": too_large,
                "error": error[:300],
            }
        except subprocess.TimeoutExpired:
            return {
                "http_status": 0, "content_type": "", "effective_url": url,
                "body": b"", "body_bytes": 0, "too_large": False,
                "error": f"curl timeout after {DOWNLOAD_TIMEOUT} seconds",
            }


def extract_pdf_text(body):
    reader = PdfReader(io.BytesIO(body), strict=False)
    chunks = []
    for page in reader.pages[:4]:
        text = clean(page.extract_text() or "")
        if text:
            chunks.append(text)
    return {
        "page_count": len(reader.pages),
        "extracted_pages": min(4, len(reader.pages)),
        "text_sample": "\n".join(chunks)[:5000],
        "text_characters": sum(len(chunk) for chunk in chunks),
    }


def main():
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    page = fetch_page(CHANNEL_URL)
    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "channel": CHANNEL_URL,
        "page_http_status": page["http_status"],
        "page_content_type": page["content_type"],
        "page_body_bytes": page["body_bytes"],
        "page_error": page["error"],
        "pdf_candidates_found": 0,
        "pdfs_tested": [],
    }
    if page["http_status"] != 200 or not page["body"]:
        report["conclusion"] = "The public channel preview could not be loaded from the runner."
        OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("CODAL360_PDF_PROBE_FAILED")
        print(report["conclusion"])
        print(f"OUTPUT_JSON:{OUTPUT}")
        return

    parser = TelegramPdfLinks()
    parser.feed(page["body"].decode("utf-8", "replace"))
    candidates = []
    seen = set()
    for item in parser.links:
        if not is_pdf_candidate(item):
            continue
        if item["url"] in seen:
            continue
        seen.add(item["url"])
        candidates.append(item)
    # Prefer links with an explicit PDF label; restrict tests to three distinct files.
    candidates.sort(key=lambda x: 0 if "pdf" in norm(x.get("title", "")) else 1)
    report["pdf_candidates_found"] = len(candidates)

    for item in candidates[:MAX_PDFS]:
        result = download_pdf(item["url"])
        body = result["body"]
        has_pdf_magic = body.startswith(b"%PDF-")
        extracted = {}
        parse_error = ""
        if result["http_status"] == 200 and has_pdf_magic and not result["too_large"]:
            try:
                extracted = extract_pdf_text(body)
            except Exception as exc:
                parse_error = f"{type(exc).__name__}: {exc}"[:300]
        status_record = {
            "url": item["url"],
            "title": item.get("title", ""),
            "source_post_id": item.get("post_id", ""),
            "http_status": result["http_status"],
            "content_type": result["content_type"],
            "effective_url": result["effective_url"],
            "body_bytes": result["body_bytes"],
            "pdf_magic_valid": has_pdf_magic,
            "error": result["error"],
            "pdf_parse_error": parse_error,
            "pdf_text": extracted,
        }
        report["pdfs_tested"].append(status_record)
        time.sleep(0.2)

    readable = sum(
        1 for item in report["pdfs_tested"]
        if item["http_status"] == 200 and item["pdf_magic_valid"] and item["pdf_text"].get("text_characters", 0) > 0
    )
    report["conclusion"] = {
        "pdf_links_tested": len(report["pdfs_tested"]),
        "valid_pdf_downloads": sum(1 for item in report["pdfs_tested"] if item["http_status"] == 200 and item["pdf_magic_valid"]),
        "pdfs_with_extractable_text": readable,
        "can_use_pdf_route_for_fundamentals": readable > 0,
        "note": "Three links or fewer were tested; a successful single file does not prove all Codal attachments are accessible.",
    }
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("CODAL360_PDF_PROBE_SUMMARY")
    print(f"CHANNEL_HTTP:{page['http_status']}")
    print(f"PDF_CANDIDATES_FOUND:{len(candidates)}")
    print(f"PDF_LINKS_TESTED:{len(report['pdfs_tested'])}")
    print(f"VALID_PDF_DOWNLOADS:{report['conclusion']['valid_pdf_downloads']}")
    print(f"PDFS_WITH_EXTRACTABLE_TEXT:{readable}")
    for item in report["pdfs_tested"]:
        print(f"PDF:{item['source_post_id']} HTTP:{item['http_status']} MAGIC:{item['pdf_magic_valid']} BYTES:{item['body_bytes']} ERROR:{item['error']}")
    print(f"OUTPUT_JSON:{OUTPUT}")
    print(f"DECISION:{report['conclusion']['can_use_pdf_route_for_fundamentals']}")


if __name__ == "__main__":
    main()
