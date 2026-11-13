"""Run 02_extract.py over every downloaded archive, N at a time, until the download finishes.
Usage: python -I scripts/03_extract_all.py raw/ work/ logs/download.log [workers]
"""
import glob, os, subprocess, sys, time

raw, work, dlog = sys.argv[1], sys.argv[2], sys.argv[3]
workers = int(sys.argv[4]) if len(sys.argv) > 4 else 10
here = os.path.dirname(os.path.abspath(__file__))
running = {}
while True:
    for host, p in list(running.items()):
        if p.poll() is not None:
            print("finished", host, p.returncode, flush=True)
            del running[host]
    ready = sorted(glob.glob(os.path.join(raw, "*.7z.done")), key=lambda f: -os.path.getsize(f[:-5]))   # biggest (Stack Overflow) first
    todo = [f[:-5] for f in ready
            if not os.path.exists(os.path.join(work, os.path.basename(f)[:-8], "DONE"))
            and os.path.basename(f)[:-8] not in running]
    for arch in todo:
        if len(running) >= workers:
            break
        host = os.path.basename(arch)[:-3]
        log = open(os.path.join(work, f"{host}.log"), "w")
        running[host] = subprocess.Popen([sys.executable, "-I", os.path.join(here, "02_extract.py"), arch, work],
                                         stdout=log, stderr=subprocess.STDOUT)
    dl_done = "ALL DONE" in open(dlog).read()
    if dl_done and not running and not todo:
        print("ALL EXTRACTED", flush=True)
        break
    time.sleep(10)
