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
        row={"name":name,**fetch(url)}
        results.append(row)
        print(f"ENDPOINT:{name} HTTP:{row['http_status']} BYTES:{row['body_bytes']} JSON:{row['json_parsed']} SUCCESS:{row['json_success']} MESSAGE:{row['response_message'][:150]} ERROR:{row['error']}")
        time.sleep(0.2)
    ok=[x for x in results if x["http_status"]==200 and x["json_success"] is True]
    report={
        "generated_at_utc":datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "purpose":"Unauthenticated GET probes of routes referenced in Fundai public JS; no bypass or paid API.",
        "results":results,
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
