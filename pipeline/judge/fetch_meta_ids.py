"""Fetch YouTube snippet/contentDetails/statistics for ids (first column of a TSV), 50 per call, resumable. Needs YOUTUBE_API_KEY."""
import os, sys, json, time, urllib.request, urllib.parse
key=os.environ["YOUTUBE_API_KEY"]; src, out = sys.argv[1], sys.argv[2]
done=set()
if os.path.exists(out):
    for l in open(out): done.add(json.loads(l)["id"])
ids=[l.split("\t")[0].strip() for l in open(src) if l.strip()]
ids=[i for i in dict.fromkeys(ids) if i not in done]
print(f"todo {len(ids)} (done {len(done)})", flush=True)
seen=0; missing=0
with open(out,"a") as o:
    for i in range(0,len(ids),50):
        chunk=ids[i:i+50]
        q=urllib.parse.urlencode({"part":"snippet,contentDetails,statistics","id":",".join(chunk),"key":key,"maxResults":50})
        for t in range(4):
            try:
                data=json.load(urllib.request.urlopen("https://www.googleapis.com/youtube/v3/videos?"+q, timeout=60)); break
            except Exception as e:
                print("retry", t, str(e)[:120], flush=True); time.sleep(5*(t+1))
        else:
            print("GIVING UP at", i, flush=True); break
        got={it["id"] for it in data.get("items",[])}
        for it in data.get("items",[]):
            sn=it["snippet"]; cd=it["contentDetails"]; st=it.get("statistics",{})
            o.write(json.dumps({"id":it["id"],"title":sn.get("title"),"channel":sn.get("channelTitle"),"channel_id":sn.get("channelId"),"published":sn.get("publishedAt"),"category":sn.get("categoryId"),"lang":sn.get("defaultAudioLanguage") or sn.get("defaultLanguage"),"duration":cd.get("duration"),"embeddable":it.get("status",{}).get("embeddable"),"views":st.get("viewCount"),"likes":st.get("likeCount"),"tags":(sn.get("tags") or [])[:10],"desc":(sn.get("description") or "")[:300]},ensure_ascii=False)+"\n")
        for v in chunk:
            if v not in got: o.write(json.dumps({"id":v,"missing":True})+"\n"); missing+=1
        seen+=len(chunk)
        if seen%5000==0: print(f"{seen} fetched, {missing} missing", flush=True)
print(f"DONE {seen} fetched, {missing} missing", flush=True)
