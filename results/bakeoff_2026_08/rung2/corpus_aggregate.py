import json, os, sys, yaml, csv
sys.path.insert(0, "health-defect")
BENCH="health-defect"; RES="results"
cfg=yaml.safe_load(open(f"{BENCH}/config.yaml",encoding="utf-8"))
repos=cfg["repos"]

def norm(p): return p.replace("\\","/").lstrip("./")

def auc(scores, labels):
    # AUC that health score is LOW for defective files -> rank by -score
    pairs=sorted(zip(scores,labels))
    pos=[s for s,l in zip(scores,labels) if l]; neg=[s for s,l in zip(scores,labels) if not l]
    if not pos or not neg: return None
    # P(score_defective < score_clean)
    import bisect
    negs=sorted(neg); n=len(negs); tot=0.0
    for p in pos:
        lo=bisect.bisect_left(negs,p); hi=bisect.bisect_right(negs,p)
        tot += (n-hi) + 0.5*(hi-lo)
    return tot/(len(pos)*n)

rows=[]
for r in repos:
    name=r["name"]; lang=r.get("language","?")
    d=os.path.join(RES,f"health_defect_{name}")
    jp=os.path.join(d,"joined_data.json")
    out={"repo":name,"language":lang}
    if not os.path.exists(jp):
        out.update({"status":"EMPTY (no joined_data.json)"}); rows.append(out); continue
    joined=json.load(open(jp,encoding="utf-8"))
    out["files"]=len(joined)
    for label in ("keyword","szz","szz_b"):
        lp=os.path.join(d,f"defect_counts_{label}.json")
        if not os.path.exists(lp):
            out[f"pos_{label}"]=None; out[f"auc_{label}"]=None; continue
        counts={norm(k):v for k,v in json.load(open(lp,encoding="utf-8")).items()}
        labels=[1 if counts.get(norm(x["file_path"]),0)>0 else 0 for x in joined]
        out[f"pos_{label}"]=sum(labels)
        out[f"auc_{label}"]=auc([x["health_score"] for x in joined], labels)
    # what correlation.json (the stale intermediate) reports
    cp=os.path.join(d,"correlation.json")
    if os.path.exists(cp):
        c=json.load(open(cp,encoding="utf-8"))
        out["corrjson_pos"]=c.get("descriptive",{}).get("n_with_defects")
    out["status"]="ok"
    rows.append(out)

hdr=f"{'repo':<12}{'lang':<11}{'files':>6}{'pos_kw':>7}{'AUC_kw':>8}{'pos_szz':>8}{'AUC_szz':>8}{'corr.json pos':>14}"
print(hdr); print("-"*len(hdr))
for o in rows:
    if o.get("status","").startswith("EMPTY"):
        print(f"{o['repo']:<12}{o['language']:<11}{'EMPTY':>6}"); continue
    def f(v,n=3): return "   -  " if v is None else f"{v:.{n}f}"
    print(f"{o['repo']:<12}{o['language']:<11}{o['files']:>6}{o['pos_keyword']:>7}{f(o['auc_keyword']):>8}{o['pos_szz']:>8}{f(o['auc_szz']):>8}{str(o.get('corrjson_pos')):>14}")
ok=[o for o in rows if o.get("status")=="ok"]
nc=[o for o in ok if o["repo"]!="cockroach"]
print("-"*len(hdr))
print(f"{'TOTAL 22':<23}{sum(o['files'] for o in ok):>6}{sum(o['pos_keyword'] for o in ok):>7}")
print(f"{'TOTAL 21 (no cockroach)':<23}{sum(o['files'] for o in nc):>6}{sum(o['pos_keyword'] for o in nc):>7}")
print(f"languages (21): {len(set(o['language'] for o in nc))}  -> {sorted(set(o['language'] for o in nc))}")
with open("results/bakeoff_2026_08/rung2/corpus_table.csv","w",newline="",encoding="utf-8") as fh:
    w=csv.DictWriter(fh, fieldnames=list(rows[0].keys())+["status"], extrasaction="ignore"); w.writeheader(); w.writerows(rows)
print("\nwrote results/bakeoff_2026_08/rung2/corpus_table.csv")
