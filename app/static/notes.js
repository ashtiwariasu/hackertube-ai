(function () {
  var box = document.getElementById("notes");
  if (!box) return;
  var vid = box.getAttribute("data-vid"), HT = window.HT || {};
  function q(s) { return box.querySelector(s); }
  function ls(k, d) { try { var v = JSON.parse(localStorage.getItem(k)); return v === null || v === undefined ? d : v; } catch (e) { return d; } }
  function lset(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} }
  function track(n, p) { if (window.htTrack) window.htTrack(n, p || {}); }
  function post(url, body) {
    return fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
      .then(function (r) { return r.json().catch(function () { return {}; }).then(function (d) { d._status = r.status; return d; }); });
  }
  // a random id for this browser, so visitors who aren't signed in can be told apart (the server keeps only a salted hash)
  var did = ls("ht_did", "");
  if (!/^[0-9a-f]{32}$/.test(did)) {
    try { var a = new Uint8Array(16); crypto.getRandomValues(a); did = Array.prototype.map.call(a, function (b) { return ("0" + b.toString(16)).slice(-2); }).join(""); lset("ht_did", did); } catch (e) { did = ""; }
  }
  var via = (location.search.match(/[?&]via=(r[a-z2-7]{6})/) || [])[1] || "";
  var vias = ls("ht_via", {});
  if (via) { vias[vid] = via; lset("ht_via", vias); track("via_open", { vid: vid }); } else { via = vias[vid] || ""; }

  var wts = ls("ht_wt", {}), wt = "", real = false, judgedOk = false, state = null, picked = "", acc = 0;

  function err(m) { var e = q(".nb-err"); e.textContent = m || ""; e.hidden = !m; }
  function done(html) { var d = q(".nb-done"); d.innerHTML = html || ""; d.hidden = !html; }
  function esc(s) { var d = document.createElement("div"); d.textContent = s; return d.innerHTML; }

  function render() {
    var s = state || {};
    var showVerdict = !!state && !s.verdict && judgedOk;
    q(".nb-verdict").hidden = !showVerdict;
    q('[data-verdict="rec"]').disabled = !real;
    q(".nb-lock").hidden = real;
    var j = q(".nb-judge");
    j.hidden = !s.judge;
    if (s.judge) {
      q(".nb-jstance").textContent = s.judge.stance === "skip" ? "Says skip" : "Recommends";
      var extra = (s.judge.needs ? " You need first: " + s.judge.needs + "." : "") + (s.judge.moment_s ? " Worth jumping to " + s.judge.moment_s + "." : "");
      q(".nb-jtext").textContent = s.judge.text + extra;
    }
    var w = q(".nb-write-link"), spot = s.spot || null;
    var noSpot = !!spot && !spot.held && !spot.open_left;   // one of this week's 10 with every review spot taken
    w.hidden = !(s.hacker && !s.note) || !q(".nb-wform").hidden || noSpot;
    w.textContent = s.paid_here ? "Write your paid review" : (spot ? "Write an unpaid review (it builds your level)" : "Write a review");
    q(".nb-paidtag").hidden = !s.paid_here;
    q(".nb-paidline").hidden = !s.paid_here;
    q(".nb-openline").hidden = !(spot && !s.paid_here);
    q(".nb-norate").hidden = !s.no_rate;
    q(".nb-list").innerHTML = s.html || "";
    if (s.html && !render.listed) { render.listed = 1; track("notes_list_viewed", { vid: vid }); }
    var visible = showVerdict || !!s.judge || !w.hidden || !q(".nb-wform").hidden || !!s.html || !q(".nb-done").hidden || !!s.no_rate;
    if (visible && box.hidden) track("notes_box_shown", { vid: vid, verdict: showVerdict, judge: !!s.judge });
    box.hidden = !visible;
  }

  function loadState() {
    return post("/api/notes/state", { vid: vid, did: did, wt: wt, via: via }).then(function (d) { if (d.ok) { state = d; render(); } }).catch(function () {});
  }

  function beat() {
    var p = window.htPlayer, pos = 0;
    try { pos = p && p.getCurrentTime ? p.getCurrentTime() : 0; } catch (e) {}
    var body = { vid: vid, pos: pos, played: acc };
    acc = 0;
    if (wt) body.wt = wt; else if (wts[vid]) body.rc = wts[vid];
    return post("/api/watch/beat", body).then(function (d) {
      if (!d.ok) return;
      wt = d.wt;
      wts = ls("ht_wt", {}); wts[vid] = d.wt;
      var keys = Object.keys(wts); if (keys.length > 60) delete wts[keys[0]];
      lset("ht_wt", wts);
      var wasReal = real, wasJudged = judgedOk;
      real = !!d.real; judgedOk = !!d.judged;
      if (real && !wasReal) track("watch_verified", { vid: vid });
      if ((real && !wasReal) || (judgedOk && !wasJudged)) loadState();  // a few minutes in, a review to rate may appear
      else if (judgedOk !== wasJudged) render();
    }).catch(function () {});
  }
  setInterval(function () {
    var p = window.htPlayer, on = false;
    try { on = !!(p && p.getPlayerState && p.getPlayerState() === 1); } catch (e) {}
    if (on) { acc += 5; if (acc >= 30) beat(); } else if (acc > 0) { beat(); }
  }, 5000);
  beat().then(loadState);

  function after(d, kind) {
    if (!d.ok) { err(d.error || "Something went wrong. Try again."); track("notes_error", { vid: vid, kind: kind, status: d._status }); return false; }
    err("");
    state = d;
    var msg = "";
    if (d.mod === "pending") msg = "<p class='fine'>Thanks. Your review shows once it has been checked, usually within a minute.</p>";
    else if (d.mod === "hidden") msg = "<p class='fine'>Thanks. Your review was held back by the automatic check, so only you can see it.</p>";
    if (!d.signed_in && kind === "verdict") { lset("ht_notes_anon", 1); msg += "<p class='fine'><a href='/auth/login?next=" + encodeURIComponent(location.pathname) + "'>Sign in</a> to keep it on your profile and rate other people's reviews.</p>"; }
    else if (d.earn && d.note && kind === "verdict") { msg += "<p class='fine'>Partners get monetised for reviews like this. <a href='/earn' class='nb-earn' data-from='prompt'>Get monetised</a></p>"; track("earn_prompt_shown", { vid: vid }); }
    if (d.share) msg += "<p class='fine'>Share your review: <input class='share-link' readonly value='" + esc(d.share) + "'> <button class='btn ghost nb-copy' type='button'>Copy link</button></p>";
    done(msg);
    render();
    return true;
  }

  box.addEventListener("click", function (e) {
    var b = e.target.closest("[data-verdict]");
    if (b && !b.disabled) {
      picked = b.getAttribute("data-verdict");
      track("verdict_started", { vid: vid, verdict: picked });
      if (state && state.hacker) { submitVerdict(""); return; }  // Hackers give a plain verdict; their notes go in paid reviews
      var f = q(".nb-form"); f.hidden = false;
      q(".nb-label").textContent = picked === "rec" ? "Who is it for, and why?" : "Who should skip it, and why? (optional)";
      f.querySelector("textarea").focus();
      return;
    }
    var v = e.target.closest("[data-vote]");
    if (v && state && state.judge) { judge(v.getAttribute("data-vote")); return; }
    var w = e.target.closest(".nb-write-link");
    if (w) { e.preventDefault(); q(".nb-wform").hidden = false; render(); track("review_started", { vid: vid, paid: !!(state && state.paid_here) }); return; }
    var c = e.target.closest(".nb-copy");
    if (c) { var i = box.querySelector(".share-link"); try { navigator.clipboard.writeText(i.value); } catch (x) { i.select(); document.execCommand("copy"); } c.textContent = "Copied"; track("share_copied", { vid: vid }); }
  });

  var ta = q(".nb-form textarea");
  ta.addEventListener("input", function () { q(".nb-count").textContent = ta.value.length + " / 300"; });

  function submitVerdict(text) {
    return post("/api/verdict", { wt: wt, verdict: picked, text: text, did: did, via: via }).then(function (d) {
      if (after(d, "verdict")) { q(".nb-form").hidden = true; track("verdict_posted", { vid: vid, verdict: picked, note: text.length > 0, chars: text.length, signed_in: !!d.signed_in, via: !!via, mod: d.mod || "" }); }
    }).catch(function () { err("Network error. Try again."); });
  }
  q(".nb-form").addEventListener("submit", function (e) { e.preventDefault(); submitVerdict(ta.value.trim()); });

  q(".nb-wform").addEventListener("submit", function (e) {
    e.preventDefault();
    var f = e.target, stance = (f.querySelector("input[name=nb-stance]:checked") || {}).value;
    var body = { vid: vid, did: did, wt: wt, via: via, stance: stance, text: q("#nb-wtext").value.trim(), needs: q("#nb-needs").value.trim(),
                 moment: q("#nb-moment").value.trim(), seen: q(".nb-seen").checked };
    post("/api/review", body).then(function (d) {
      if (after(d, "review")) { f.hidden = true; track("review_posted", { vid: vid, stance: stance, chars: body.text.length, needs: !!body.needs, moment: !!body.moment, paid: !!(state && state.paid_here) }); }
    }).catch(function () { err("Network error. Try again."); });
  });

  function judge(vote) {
    var id = state.judge.id;
    post("/api/judge", { note_id: id, vote: vote, did: did, wt: wt, via: via }).then(function (d) {
      if (!after(d, "judge")) return;
      var t = d.tally || {};
      var said = vote === "agree" ? "agreed" : vote === "disagree" ? "disagreed" : "said you're not sure";
      var tq = q(".nb-tally");
      tq.textContent = "You " + said + ". " + t.agree + " of " + (t.judged >= t.panel ? t.panel + " viewers agree." : t.judged + " viewers agree so far.");
      tq.hidden = false;
      track("judgment_posted", { vid: vid, vote: vote });
    }).catch(function () { err("Network error. Try again."); });
  }
})();
