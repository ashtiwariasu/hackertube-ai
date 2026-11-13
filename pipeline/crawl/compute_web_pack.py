"""Web pack rows (packs/PACKS.md "Web pack") for a set of target videos from one crawl's agg/pairs.tsv.
domains = distinct registrable linking sites, YouTube family excluded, heavy linkers (>CAP pairs) counted once in total;
top_domains = up to 5 sites, Tranco-ranked first. months=1 for a single snapshot."""
import sys, json, collections, csv, os
d, cap, month, targets_path, out_path = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4], sys.argv[5]
# sixth arg: either a JSON list of curated ids (phase B rules) or a gate JSONL (phase W: rows with vid, keep, english, kind, area, why)
curated, gate = set(), {}
if len(sys.argv) > 6:
    if sys.argv[6].endswith(".jsonl"):
        for line in open(sys.argv[6]):
            g = json.loads(line)
            if g.get("keep") and g.get("english"):
                curated.add(g["vid"]); gate[g["vid"]] = g
    else:
        curated = set(json.load(open(sys.argv[6])))
YT = ("youtube.com", "youtu.be", "youtube-nocookie.com")
CC2 = {"co.uk","com.au","org.uk","co.jp","com.br","co.nz","ac.uk","gov.uk","co.za","com.mx","org.au","edu.au","ne.jp","or.jp","co.in","co.kr","com.tr","com.ar","gov.au","net.au","ac.jp","com.sg","com.hk","co.il","com.tw","edu.cn","com.cn","org.br"}
SHARED = {"blogspot.com","wordpress.com","wordpress.org","tumblr.com","wixsite.com","weebly.com","medium.com","github.io","github.com","substack.com","livejournal.com","amazonaws.com","readthedocs.io","google.com","googleblog.com","sites.google.com","ok.ru","vk.com","facebook.com","twitter.com","x.com","reddit.com","pinterest.com","linkedin.com","cloudfront.net","archive.org","blog.jp","hatenablog.com","note.com","fc2.com","seesaa.net","over-blog.com","canalblog.com","blog.hu","wix.com","squarespace.com","ghost.io","beehiiv.com","buttondown.com","netlify.app","vercel.app","pages.dev","herokuapp.com","web.app","firebaseapp.com","000webhostapp.com","altervista.org","blogg.se","blogg.no","bloggfabriken.se","blogfa.com","mihanblog.com","blog.me","tistory.com","naver.com","daum.net","exblog.jp","ameblo.jp","livedoor.jp","goo.ne.jp","yahoo.co.jp","yahoo.com","msn.com","bing.com","googleusercontent.com","atlassian.net","t.me","azurewebsites.net","windows.net","cloudflare.com","akamaihd.net","s3.amazonaws.com","dropbox.com","box.com","notion.site","gitbook.io","gitlab.io","bitbucket.io","hubspot.com","hubspotusercontent.com","mailchimp.com","mailchi.mp","constantcontact.com","sendgrid.net","wp.com","wpengine.com","kinsta.cloud","myshopify.com","shopify.com","bigcartel.com","strikingly.com","carrd.co","webflow.io","yolasite.com","jimdo.com","jimdosite.com","webnode.com","site123.me","weblium.site","godaddysites.com","duckdns.org","ddns.net","no-ip.org","yumpu.com","issuu.com","scribd.com","slideshare.net","prezi.com","padlet.com","wakelet.com","pearltrees.com","diigo.com","pinterest.co.uk","flipboard.com","paper.li","scoop.it","academia.edu","researchgate.net","semanticscholar.org"}
def is_yt(dom): return any(dom == y or dom.endswith("." + y) for y in YT)
def reg(dom):
    p = dom.split(".")
    if len(p) >= 3 and ".".join(p[-2:]) in CC2: return ".".join(p[-3:])
    return ".".join(p[-2:]) if len(p) > 2 else dom
tranco = {}
with open(os.path.expanduser("~/cc/tranco.csv")) as f:
    for rank, dom in csv.reader(f): tranco[dom] = int(rank)
targets = set(json.load(open(targets_path)))
pairs_by_dom = collections.Counter()
with open(f"{d}/agg/pairs.tsv") as f:
    for l in f:
        v, dom = l.rstrip("\n").split("\t")
        if is_yt(dom): continue
        pairs_by_dom[reg(dom)] += 1
heavy = {x for x, n in pairs_by_dom.items() if n > cap}
light = collections.defaultdict(set); hv = collections.defaultdict(set)
with open(f"{d}/agg/pairs.tsv") as f:
    for l in f:
        v, dom = l.rstrip("\n").split("\t")
        if v not in targets or is_yt(dom): continue
        r = reg(dom)
        (hv if r in heavy else light)[v].add(r)
n = 0
with open(out_path, "w") as o:
    for v in targets:
        doms = light.get(v, set()); hd = hv.get(v, set())
        count = len(doms) + (1 if hd else 0)
        if count < 1: continue
        rep = {x for x in (doms | hd) if x not in SHARED and tranco.get(x, 10**9) <= 100000}
        reputable = len(rep - heavy) + (1 if rep & heavy else 0)
        ranked = sorted((doms | hd) - SHARED, key=lambda x: (tranco.get(x, 10**9), x))   # shared hosts never name a source
        if len(ranked) < 2: ranked += sorted((doms | hd) & SHARED, key=lambda x: (tranco.get(x, 10**9), x))
        o.write(json.dumps({"vid": v, "domains": count, "months": 1, "first_seen": month, "last_seen": month, "top_domains": ranked[:5], "reputable": reputable, "curated": v in curated, **({"why": gate[v]["why"], "kind": gate[v]["kind"], "area": gate[v]["area"]} if v in gate else {})}) + "\n"); n += 1
print(f"targets {len(targets)} rows {n} heavy {len(heavy)}")
