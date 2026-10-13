#!/usr/bin/env python3
"""Inspect Fundai's public front-end for accessible public data routes.

Only requests the public landing page and linked static JS. No login, no
authentication bypass, no form submission, and no guessed endpoint requests.
"""
from __future__ import annotations
import json, re, subprocess, tempfile, time
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"data"/"fundai_source_probe.json"
UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36"
MAX_PAGE=2*1024*1024
TIMEOUT=8

class AssetParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title=False; self.title_text=[]; self.scripts=[]
    def handle_starttag(self, tag, attrs):
        a=dict(attrs)
        if tag.lower()=="title": self.title=True
        if tag.lower()=="script" and a.get("src"): self.scripts.append(a["src"])
    def handle_endtag(self,tag):
        if tag.lower()=="title": self.title=False
    def handle_data(self,data):
        if self.title: self.title_text.append(data)

def fetch(url):
    with tempfile.NamedTemporaryFile(prefix="fundai-probe-",suffix=".bin") as temp:
        command=["curl","--location","--http1.1","--connect-timeout","4","--max-time",str(TIMEOUT),
                 "--max-filesize",str(MAX_PAGE),"--retry","0","--user-agent",UA,
                 "--header","Accept: text/html,application/javascript,*/*;q=0.8",
                 "--silent","--show-error","--output",temp.name,
                 "--write-out","%{http_code}|%{content_type}|%{url_effective}",url]
        start=time.monotonic()
        try:
            run=subprocess.run(command,capture_output=True,text=True,timeout=TIMEOUT+2,check=False)
            fields=(run.stdout or "").strip().split("|",2)
            status=fields[0] if fields and fields[0] else "ERROR"
            try: status=int(status)
            except ValueError: pass
            temp.flush()
            size=Path(temp.name).stat().st_size
            with open(temp.name,"rb") as f: body=f.read(MAX_PAGE+1)
            err=(run.stderr or "").strip()
            if size>MAX_PAGE or run.returncode==63: err=f"response exceeds {MAX_PAGE} byte safety cap"
            elif run.returncode and not err: err=f"curl exit code {run.returncode}"
            return {"url":url,"effective_url":fields[2] if len(fields)>2 else url,
                    "http_status":status,"content_type":fields[1] if len(fields)>1 else "",
                    "body_bytes":len(body),"elapsed_seconds":round(time.monotonic()-start,2),
                    "curl_exit_code":run.returncode,"error":err[:350],
                    "text":body.decode("utf-8-sig","replace")}
        except subprocess.TimeoutExpired:
            temp.flush()
            try:
                with open(temp.name,"rb") as f: body=f.read(MAX_PAGE+1)
            except Exception: body=b""
            return {"url":url,"effective_url":url,"http_status":0,"content_type":"",
                    "body_bytes":len(body),"elapsed_seconds":round(time.monotonic()-start,2),
                    "curl_exit_code":"TIMEOUT","error":f"curl timeout after {TIMEOUT}s",
                    "text":body.decode("utf-8-sig","replace")}

def main():
    OUT.parent.mkdir(parents=True,exist_ok=True)
    root=fetch("https://fundai.ir/")
    parser=AssetParser()
    parser.feed(root.get("text",""))
    title=" ".join(parser.title_text).strip()
    script_urls=[]
    for raw in parser.scripts:
        url=urljoin(root.get("effective_url") or root["url"],raw)
        if urlparse(url).scheme in ("http","https") and url not in script_urls:
            script_urls.append(url)
    asset_urls=script_urls[:18]
    assets=[]
    all_text=[root.get("text","")]
    for url in asset_urls:
        result=fetch(url)
        text=result.get("text","")
        all_text.append(text)
        paths=[]
        patterns=[
            r"""["'](/api/[A-Za-z0-9_?&=./{}\-]+)["']""",
            r"""["'](/(?:graphql|trpc|data)/[A-Za-z0-9_?&=./{}\-]+)["']""",
            r"""https?://[A-Za-z0-9._:-]+/(?:api|graphql|trpc)/[A-Za-z0-9_?&=./{}\-]+""",
        ]
        for pat in patterns:
            for hit in re.findall(pat,text):
                if hit not in paths: paths.append(hit)
        assets.append({
            "url":url,"http_status":result["http_status"],"body_bytes":result["body_bytes"],
            "content_type":result["content_type"],"elapsed_seconds":result["elapsed_seconds"],
            "error":result["error"],"api_path_mentions":paths[:120],
            "contains_fundai_stock_terms":bool(re.search(r"(?:margin|profit|financial|income statement|P/E|ROE|EPS|سود خالص|حاشیه سود)",text,re.I))
        })
        time.sleep(0.15)

    combined="\n".join(all_text)
    # Capture small source excerpts around actual request functions and data path
    # strings to understand the public app's client wiring without guessing endpoints.
    source_contexts = {}
    combined_lower = combined.lower()
    for needle in ("fetch(", "axios", "baseurl", "xmlhttprequest", "graphql", "/api", "stock", "financial", "ratio", "filter", "statement", "report"):
        contexts = []
        start = 0
        while len(contexts) < 8:
            at = combined_lower.find(needle, start)
            if at < 0: break
            lo, hi = max(0, at - 220), min(len(combined), at + 360)
            contexts.append(combined[lo:hi].replace("\\n", " ")[:600])
            start = at + len(needle)
        if contexts: source_contexts[needle] = contexts
    quoted_route_strings = []
    for match in re.finditer(r"""["']([^"'\\r\\n]{1,180})["']""", combined):
        value = match.group(1)
        low = value.lower()
        if any(term in low for term in ("api", "graphql", "trpc", "endpoint", "financial", "statement", "ratio", "filter", "stock")):
            if value not in quoted_route_strings:
                quoted_route_strings.append(value)
    unique_api=[]
    for pat in [r"""["'](/api/[A-Za-z0-9_?&=./{}\-]+)["']""",
                r"""["'](/(?:graphql|trpc|data)/[A-Za-z0-9_?&=./{}\-]+)["']"""]:
        for hit in re.findall(pat,combined):
            if hit not in unique_api: unique_api.append(hit)

    report={
        "generated_at_utc":datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "purpose":"Public front-end source probe; no login, no guessed endpoint requests, no paid API.",
        "landing_page":{
            "url":root["url"],"effective_url":root.get("effective_url"),
            "http_status":root["http_status"],"content_type":root["content_type"],
            "body_bytes":root["body_bytes"],"elapsed_seconds":root["elapsed_seconds"],
            "error":root["error"],"title":title,"linked_script_count":len(script_urls),
            "scripts_inspected":len(assets)
        },
        "static_assets":assets,
        "source_contexts":source_contexts,
        "quoted_data_route_or_endpoint_strings":quoted_route_strings[:250],
        "candidate_public_routes_seen_in_source":unique_api[:250],
        "conclusion":{
            "landing_page_reachable":root["http_status"]==200 and root["body_bytes"]>1000,
            "static_js_reachable_count":sum(1 for x in assets if x["http_status"]==200 and x["body_bytes"]>1000),
            "routes_found_count":len(unique_api),
            "status":"DISCOVERY_ONLY",
            "note":"A route string found in a public JS bundle is not proof it is public or suitable; test only explicit documented public routes, and never bypass authentication."
        }
    }
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("FUNDAI_SOURCE_PROBE")
    print(f"LANDING_HTTP:{report['landing_page']['http_status']}")
    print(f"LANDING_BYTES:{report['landing_page']['body_bytes']}")
    print(f"PAGE_TITLE:{title}")
    print(f"SCRIPTS_FOUND:{len(script_urls)}")
    print(f"SCRIPTS_REACHED:{report['conclusion']['static_js_reachable_count']}")
    print(f"CANDIDATE_PUBLIC_ROUTE_STRINGS:{len(unique_api)}")
    for path in unique_api[:40]: print(f"ROUTE_STRING:{path}")
    print(f"OUTPUT:{OUT.relative_to(ROOT)}")

if __name__=="__main__":
    main()
