(function () {
  var el = document.getElementById("player");
  if (!el) return;
  var vid = el.dataset.vid, start = parseInt(el.dataset.start || "0", 10), KEY = "ht_watch";
  function load() { try { return JSON.parse(localStorage.getItem(KEY)) || {}; } catch (e) { return {}; } }
  function save(m) { try { localStorage.setItem(KEY, JSON.stringify(m)); } catch (e) {} }
  var map = load(), mine = map[vid];
  // remember what this browser opened, newest first, so the home page can show "Because you opened X"
  try { var op = JSON.parse(localStorage.getItem("ht_opened")) || []; op = [vid].concat(op.filter(function (x) { return x !== vid; })).slice(0, 30); localStorage.setItem("ht_opened", JSON.stringify(op)); } catch (e) {}
  if (!start && !/[?&]t=/.test(location.search) && mine && mine.pct > 0.05 && mine.pct < 0.9 && mine.pos > 30) start = Math.floor(mine.pos);
  var player, timer = null, maxPos = (mine && mine.pos) || 0, secs = (mine && mine.secs) || 0, dur = 0, lastSent = 0, milestones = {}, played = 0, engaged = {};
  function snapshot(left) {
    if (!player || !player.getCurrentTime) return;
    var pos = player.getCurrentTime() || 0; maxPos = Math.max(maxPos, pos); dur = player.getDuration() || dur;
    var pct = dur ? Math.min(1, maxPos / dur) : 0;
    var rec = { pos: Math.round(pos), dur: Math.round(dur), pct: Math.round(pct * 1000) / 1000, secs: Math.round(secs), t: Math.floor(Date.now() / 1000) };
    map[vid] = rec; save(map);
    var body = JSON.stringify(Object.assign({ vid: vid, left: !!left }, rec));
    try {
      if (navigator.sendBeacon) navigator.sendBeacon("/api/watch", new Blob([body], { type: "text/plain" }));
      else fetch("/api/watch", { method: "POST", body: body, keepalive: true });
    } catch (e) {}
  }
  function tick() {
    if (!player || player.getPlayerState() !== 1) return;
    secs += 5; played += 5;
    // seconds actually played on this page view, not the furthest point reached (a seek or a resume does not count)
    [60, 300, 900].forEach(function (t) { if (played >= t && !engaged[t]) { engaged[t] = 1; if (window.htTrack) htTrack("watch_engaged", { vid: vid, secs: t }); } });
    var pos = player.getCurrentTime() || 0; maxPos = Math.max(maxPos, pos); dur = player.getDuration() || dur;
    var pct = dur ? maxPos / dur : 0;
    [0.25, 0.5, 0.6, 0.75, 0.9].forEach(function (m) { if (pct >= m && !milestones[m]) { milestones[m] = 1; snapshot(false); if (window.htTrack) htTrack("watch_milestone", { vid: vid, pct: m, secs: secs }); } });
    if (Date.now() - lastSent > 60000) { lastSent = Date.now(); snapshot(false); }
  }
  window.onYouTubeIframeAPIReady = function () {
    player = new YT.Player("player", {
      host: "https://www.youtube-nocookie.com", videoId: vid, width: "100%", height: "100%",
      playerVars: { rel: 0, start: start || 0, playsinline: 1 },
      events: {
        onStateChange: function (e) {
          if (e.data === 1) { clearInterval(timer); timer = setInterval(tick, 5000); lastSent = Date.now(); if (!window.__htPlayed) { window.__htPlayed = 1; if (window.htTrack) htTrack("watch_start", { vid: vid, resumed: start > 0 }); } }
          else if (e.data === 2 || e.data === 0) { clearInterval(timer); if (e.data === 0) maxPos = player.getDuration() || maxPos; snapshot(false); }
        }
      }
    });
    window.htPlayer = player;
  };
  var tag = document.createElement("script"); tag.src = "https://www.youtube.com/iframe_api"; document.head.appendChild(tag);
  document.addEventListener("visibilitychange", function () { if (document.visibilityState === "hidden") snapshot(true); });
  window.addEventListener("pagehide", function () { snapshot(true); });
})();
