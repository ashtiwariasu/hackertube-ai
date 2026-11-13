#!/bin/bash
# usage: aggregate_box.sh CRAWL  -> ~/cc/CRAWL/agg/{pairs.tsv, vid_domains.tsv, summary.txt}
C=$1; cd ~/cc/$C; mkdir -p agg; export LC_ALL=C
n=$(ls out | wc -l); echo "files=$n" > agg/summary.txt
find out -name "*.tsv" -print0 | xargs -0 cut -f1,2 | sort -u -S 30G --parallel=16 -T agg > agg/pairs.tsv          # distinct (video, domain)
echo "pairs=$(wc -l < agg/pairs.tsv)" >> agg/summary.txt
cut -f1 agg/pairs.tsv | uniq -c | awk '{print $2"\t"$1}' > agg/vid_domains.tsv   # video -> distinct domains
echo "distinct_videos=$(wc -l < agg/vid_domains.tsv)" >> agg/summary.txt
awk -F'\t' '{if($2>=2)a++; if($2>=3)b++; if($2>=5)c++; if($2>=10)d++} END{print "videos_ge2_domains="a"\nvideos_ge3_domains="b"\nvideos_ge5_domains="c"\nvideos_ge10_domains="d}' agg/vid_domains.tsv >> agg/summary.txt
cut -f2 agg/pairs.tsv | sort -S 10G --parallel=16 -T agg | uniq -c | sort -rn | head -30 > agg/top_domains.txt
echo "distinct_domains=$(cut -f2 agg/pairs.tsv | sort -u -S 10G --parallel=16 -T agg | wc -l)" >> agg/summary.txt
cat agg/summary.txt
