#!/usr/bin/env bash
# Quick health check for the Bluesky collector (run on the H100).
export XDG_RUNTIME_DIR=/run/user/$(id -u)
cd ~/packs/bluesky
systemctl --user is-active bluesky-yt-collector
systemctl --user show bluesky-yt-collector -p NRestarts -p ActiveEnterTimestamp
cat data/status.json
du -sh data/
grep -E "WARNING|ERROR" logs/collector.log | tail -5
