#!/usr/bin/env python3
"""Check only read endpoints referenced by Fundai's public JavaScript bundle.

No tokens, cookies, login, POST submissions or auth bypasses are used. This is
an unauthenticated GET probe to establish whether the site's public page can
fetch its own fundamental data from GitHub Actions.
"""
from __future__ import annotations
import json, subprocess, tempfile, time
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"data"/"fundai_api_probe.json"
UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36"
MAX_BYTES=512*1024
TIMEOUT=7
# These GET routes are explicitly constructed as GET calls in the public
# JS bundle; query-sensitive routes are not queried with invented parameters.
ROUTES=[
    ("fundamental_filters_access","https://fundai.ir/api/v1/fundamental/filters-access/"),
    ("fundamental_ratio_filters_no_parameters","https://fundai.ir/api/v1/fundamental/financial-ratio-filters/"),
    ("fundamental_ratio_history_no_parameters","https://fundai.ir/api/v1/fundamental/financial-ratio-history/"),
    ("financial_packages","https://fundai.ir/api/v1/financial/packages/"),
]

def fetch(url):
    with tempfile.NamedTemporaryFile(prefix="fundai-api-probe-",suffix=".bin") as temp:
        cmd=[
            "curl","--location","--http1.1","--connect-timeout","4","--max-time",str(TIMEOUT),
            "--max-filesize",str(MAX_BYTES),"--retry","0","--user-agent",UA,
            "--header","Accept: application/json,text/plain,*/*;q=0.8",
            "--header","Referer: https://fundai.ir/filters",
            "--silent","--show-error","--output",temp.name,
            "--write-out","%{http_code}|%{content_type}|%{url_effective}",url
        ]
        start=time.monotonic()
        try:
            run=subprocess.run(cmd,capture_output=True,text=True,timeout=TIMEOUT+2,check=False)
            parts=(run.stdout or "").strip().split("|",2)
            status=parts[0] if parts and parts[0] else "ERROR"
            try: status=int(status)
            except ValueError: pass
            temp.flush()
            with open(temp.name,"rb") as f: body=f.read(MAX_BYTES+1)
            error=(run.stderr or "").strip()
            if run.returncode and not error: error=f"curl exit code {run.returncode}"
            if run.returncode==63 or len(body)>MAX_BYTES: error=f"response exceeded {MAX_BYTES} byte cap"
            text=body.decode("utf-8-sig","replace")
            try: payload=json.loads(text)
            except Exception: payload=None
            return {
                "url":url,"effective_url":parts[2] if len(parts)>2 else url,
                "http_status":status,"content_type":parts[1] if len(parts)>1 else "",
                "body_bytes":len(body),"elapsed_seconds":round(time.monotonic()-start,2),
                "curl_exit_code":run.returncode,"error":error[:350],
                "json_parsed":payload is not None,
                "json_success":payload.get("success") if isinstance(payload,dict) else None,
                "response_keys":list(payload.keys())[:40] if isinstance(payload,dict) else [],
                "response_message":str(payload.get("message") or payload.get("detail") or "")[:500] if isinstance(payload,dict) else "",
                "data_type":type(payload.get("data")).__name__ if isinstance(payload,dict) and "data" in payload else "",
                "data_keys":list(payload["data"].keys())[:40] if isinstance(payload,dict) and isinstance(payload.get("data"),dict) else [],
                "data_count":len(payload.get("data")) if isinstance(payload,dict) and isinstance(payload.get("data"),list) else None,
                "body_excerpt":text[:900] if payload is None else "",
                "_payload":payload,
            }
        except subprocess.TimeoutExpired:
            temp.flush()
            try:
                with open(temp.name,"rb") as f: body=f.read(MAX_BYTES+1)
            except Exception: body=b""
            return {"url":url,"effective_url":url,"http_status":0,"content_type":"",
                    "body_bytes":len(body),"elapsed_seconds":round(time.monotonic()-start,2),
                    "curl_exit_code":"TIMEOUT","error":f"curl timeout after {TIMEOUT}s",
                    "json_parsed":False,"json_success":None,"response_keys":[],"response_message":"",
                    "data_type":"","data_keys":[],"data_count":None,"body_excerpt":""}

def main():
    OUT.parent.mkdir(parents=True,exist_ok=True)
    results=[]
    for name,url in ROUTES:
        raw=fetch(url)
        payload=raw.pop("_payload", None)
        row={"name":name,**raw}
        if name == "fundamental_filters_access" and isinstance(payload, dict):
            row["public_access_metadata"] = payload
        if name == "fundamental_ratio_filters_no_parameters" and isinstance(payload, dict):
            sample_rows = payload.get("results") if isinstance(payload.get("results"), list) else []
            row["results_count"] = payload.get("count")
            row["next_page_url"] = payload.get("next")
            row["previous_page_url"] = payload.get("previous")
            row["columns"] = payload.get("columns") if isinstance(payload.get("columns"), list) else payload.get("columns")
            row["first_rows_sample"] = sample_rows[:8]
            row["first_row_keys"] = list(sample_rows[0].keys()) if sample_rows and isinstance(sample_rows[0], dict) else []
        if name == "financial_packages" and isinstance(payload, (dict, list)):
            row["packages_shape"] = list(payload.keys()) if isinstance(payload,dict) else f"list:{len(payload)}"
            row["packages_sample"] = payload[:3] if isinstance(payload,list) else {k:payload[k] for k in list(payload)[:10]}
        results.append(row)
        print(f"ENDPOINT:{name} HTTP:{row['http_status']} BYTES:{row['body_bytes']} JSON:{row['json_parsed']} RESULTS:{row.get('results_count')} NEXT:{bool(row.get('next_page_url'))} MESSAGE:{row['response_message'][:150]} ERROR:{row['error']}")
        time.sleep(0.2)

    # Follow only the pagination URL explicitly returned by the live endpoint;
    # cap to 3 pages for schema discovery, and never fabricate query parameters.
    ratio_result=next((x for x in results if x["name"]=="fundamental_ratio_filters_no_parameters"),None)
    pages=[]
    next_url=ratio_result.get("next_page_url") if ratio_result else None
    page_attempt=0
    while next_url and page_attempt<2:
        page_attempt += 1
        raw=fetch(next_url)
        payload=raw.pop("_payload",None)
        page={
            "requested_url":next_url,
            "http_status":raw["http_status"],
            "body_bytes":raw["body_bytes"],
            "elapsed_seconds":raw["elapsed_seconds"],
            "error":raw["error"],
            "response_keys":raw["response_keys"],
            "results_count":None,
            "next_page_url":None,
            "first_row_keys":[],
            "first_rows_sample":[],
        }
        if isinstance(payload,dict):
            rows=payload.get("results") if isinstance(payload.get("results"),list) else []
            page["results_count"]=payload.get("count")
            page["next_page_url"]=payload.get("next")
            page["first_row_keys"]=list(rows[0].keys()) if rows and isinstance(rows[0],dict) else []
            page["first_rows_sample"]=rows[:2]
        pages.append(page)
        print(f"PAGINATION_PAGE:{page_attempt+1} HTTP:{page['http_status']} RESULTS:{page['results_count']} NEXT:{bool(page['next_page_url'])}")
        next_url=page["next_page_url"]
        time.sleep(0.2)

    ok=[x for x in results if x["http_status"]==200 and x["json_parsed"]]
    report={
        "generated_at_utc":datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "purpose":"Unauthenticated GET probes of routes referenced in Fundai public JS; no bypass or paid API.",
        "results":results,
        "pagination_schema_probe":pages,
        "conclusion":{
            "tested":len(results),
            "http_200_json_success_count":len(ok),
            "public_data_route_candidates":[x["name"] for x in ok],
            "fundai_public_data_usable_for_fundamental_integration":False,
            "note":"Even HTTP 200 is only a route-shape clue. A data source can be adopted only after obtaining representative rows and validating issuer IDs, values, units, dates and rate limits."
        }
    }
    # A data endpoint that succeeds is just a candidate; do not automatically
    # label the integration as production-ready.
    report["conclusion"]["fundai_public_data_usable_for_fundamental_integration"]=any(
        x["name"]=="fundamental_filters_access" and x["http_status"]==200 and x["json_parsed"]
        for x in results
    )
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(f"FUNDAI_API_PROBE_SUMMARY:{len(ok)}/{len(results)} endpoints returned successful JSON envelopes")
    print(f"OUTPUT:{OUT.relative_to(ROOT)}")

if __name__=="__main__":
    main()
