(function(){
  var HT=window.HT||{loggedIn:false,likes:[],later:[]};
  var KEY="ht_local_v1";
  function load(){try{return JSON.parse(localStorage.getItem(KEY))||{likes:[],later:[]}}catch(e){return {likes:[],later:[]}}}
  function save(s){try{localStorage.setItem(KEY,JSON.stringify(s))}catch(e){}}
  var local=load();
  var state={likes:new Set(HT.loggedIn?HT.likes:local.likes),later:new Set(HT.loggedIn?HT.later:local.later)};
  function toast(html){var t=document.getElementById("toast");t.innerHTML=html;t.classList.add("show");clearTimeout(t._h);t._h=setTimeout(function(){t.classList.remove("show")},5000)}
  function lst(k){try{return JSON.parse(localStorage.getItem(k))||[]}catch(e){return []}}
  function setLst(k,v){try{localStorage.setItem(k,JSON.stringify(v))}catch(e){}}
  function paint(){
    var hid=new Set(HT.loggedIn?(HT.hidden||[]):lst("ht_hidden")),sn=new Set(HT.loggedIn?(HT.seen||[]):lst("ht_seen"));
    document.querySelectorAll("[data-vid]").forEach(function(el){var vid=el.getAttribute("data-vid");
      if(el.classList.contains("card")){if(hid.has(vid)){el.remove();return}if(sn.has(vid))el.classList.add("seen-card")}
      el.querySelectorAll(".act[data-kind]").forEach(function(b){var k=b.getAttribute("data-kind");if(state[k])b.classList.toggle("on",state[k].has(vid))})})}
  // On sign-in, fold what the browser collected into the account, one request at a time (each save rewrites the profile).
  var merges=[];
  function post(url,body){return fetch(url,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)}).then(function(r){return r.json()})}
  if(HT.loggedIn&&(local.likes.length||local.later.length)){
    merges.push(function(){return post("/api/merge",local).then(function(d){if(d.ok){state.likes=new Set(d.likes);state.later=new Set(d.later);save({likes:[],later:[]});paint()}})})
  }
  if(HT.loggedIn){try{var sh=JSON.parse(localStorage.getItem("ht_shots")||"null");if(sh&&(sh.vids||sh.titles)){merges.push(function(){return post("/api/screenshot/merge",sh).then(function(d){if(d.ok){localStorage.removeItem("ht_shots");toast("Your screenshot results are saved to your profile.")}})})}}catch(e){}}
  if(HT.loggedIn){try{var pr=JSON.parse(localStorage.getItem("ht_profile")||"null");if(pr&&pr.items&&pr.items.length){merges.push(function(){return post("/api/anything/merge",pr).then(function(d){if(d.ok){localStorage.removeItem("ht_profile");toast("What you pasted is saved to your profile.")}})})}}catch(e){}}
  if(HT.loggedIn){try{var ai=JSON.parse(localStorage.getItem("ht_ai")||"null");if(ai&&ai.texts){merges.push(function(){return post("/api/ai-profile/merge",ai).then(function(d){if(d.ok){localStorage.removeItem("ht_ai");toast("Your AI profile is saved.")}})})}}catch(e){}}
  if(HT.loggedIn){try{var w=JSON.parse(localStorage.getItem("ht_watch")||"null")||{},sn=lst("ht_seen"),hd=lst("ht_hidden"),mt=parseInt(localStorage.getItem("ht_merged_t")||"0",10),fresh={};
    Object.keys(w).forEach(function(v){if((w[v].t||0)>mt)fresh[v]=w[v]});
    if(Object.keys(fresh).length||sn.length||hd.length){merges.push(function(){var at=Math.floor(Date.now()/1000);return post("/api/signals/merge",{watch:fresh,seen:sn,hidden:hd}).then(function(d){if(d.ok){localStorage.removeItem("ht_seen");localStorage.removeItem("ht_hidden");try{localStorage.setItem("ht_merged_t",String(at))}catch(e){}HT.seen=(HT.seen||[]).concat(sn);HT.hidden=(HT.hidden||[]).concat(hd);paint()}})})}}catch(e){}}
  if(HT.loggedIn){try{var nd=JSON.parse(localStorage.getItem("ht_did")||"null"),na=localStorage.getItem("ht_notes_anon");if(nd&&na){merges.push(function(){return post("/api/notes/merge",{did:nd}).then(function(d){if(d.ok){localStorage.removeItem("ht_notes_anon");if(d.moved)toast("Your verdicts and notes are now on your account.")}})})}}catch(e){}}
  if(merges.length&&window.htTrack)htTrack("signin_merge",{parts:merges.length});
  merges.reduce(function(p,f){return p.then(f).catch(function(){})},Promise.resolve());
  if(/[?&]msg=pro-welcome/.test(location.search)){var tx=document.getElementById("ht-tx"),txid=tx?tx.getAttribute("data-tx"):"";
    if(window.htTrack)htTrack("pro_welcome_shown",{value:5,currency:"USD"}); /* the purchase itself is counted once, by the server */
    if(txid&&window.gtag&&HT_AN.ga)gtag("event","purchase",{value:5,currency:"USD",transaction_id:txid,items:[{item_id:"hackertube-pro",item_name:"HackerTube Pro"}]});
    try{history.replaceState(null,"",location.pathname+location.search.replace(/([?&])msg=pro-welcome&?/,"$1").replace(/[?&]$/,"")+location.hash)}catch(e){}}
  document.addEventListener("click",function(e){var a=e.target.closest&&e.target.closest('a[href^="/earn"]');if(a&&window.htTrack)htTrack("earn_link_click",{from:a.getAttribute("data-from")||"other",path:location.pathname})});
  document.addEventListener("submit",function(e){var f=e.target;if(f&&f.getAttribute&&f.getAttribute("action")==="/pro/checkout"&&window.htTrack)htTrack("checkout_started",{value:5,currency:"USD"})});
  document.addEventListener("click",function(e){
    var a=e.target.closest(".card a[href^='/v/']");
    if(a&&window.htTrack){var cd=a.closest(".card"),rw=a.closest("[data-row]"),grid=cd&&cd.parentElement;
      htTrack("card_open",{vid:cd?cd.getAttribute("data-vid"):"",row:rw?rw.getAttribute("data-row"):"other",pos:grid?Array.prototype.indexOf.call(grid.children,cd):-1,path:location.pathname})}
  });
  document.addEventListener("click",function(e){
    var b=e.target.closest(".act[data-kind]");if(!b)return;
    e.preventDefault();
    var host=b.closest("[data-vid]");var vid=host.getAttribute("data-vid");var kind=b.getAttribute("data-kind");
    if(window.htTrack)htTrack("card_action",{kind:kind,logged_in:!!HT.loggedIn});
    if(kind==="seen"||kind==="hidden"){
      var card=b.closest(".card");
      if(HT.loggedIn){fetch("/api/feedback",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({kind:kind,vid:vid,on:true})}).then(function(r){return r.json()}).then(function(d){if(d.ok&&kind==="seen"&&typeof d.seen_ess==="number"){toast("Marked as seen. You've now seen "+d.seen_ess+" of 500 essentials.")}}).catch(function(){})}
      else{var k=kind==="seen"?"ht_seen":"ht_hidden";var a=lst(k);if(a.indexOf(vid)<0){a.push(vid);setLst(k,a.slice(-1000))}}
      if(HT.loggedIn){var hk=kind==="seen"?"seen":"hidden";HT[hk]=(HT[hk]||[]).concat([vid])}
      if(kind==="hidden"){if(card){card.style.transition="opacity .3s";card.style.opacity="0";setTimeout(function(){card.remove()},300)}toast("Hidden. It won't be recommended again.")}
      else{if(card)card.classList.add("seen-card");if(!HT.loggedIn)toast("Marked as seen. It won't be recommended, and it counts toward your essentials.")}
      return;
    }
    var on=!state[kind].has(vid);
    if(on)state[kind].add(vid);else state[kind].delete(vid);
    paint();
    if(HT.loggedIn){fetch("/api/toggle",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({kind:kind,vid:vid,on:on})}).catch(function(){})}
    else{save({likes:Array.from(state.likes),later:Array.from(state.later)});
      var n=state.likes.size+state.later.size;
      if(on&&(n===1||n%5===0))toast('Saved in this browser. <a href="/auth/youtube?next=/me">Connect YouTube</a> to sync and get your own feed.');}
  });
  document.addEventListener("keydown",function(e){if(e.key==="/"&&!/input|textarea/i.test(document.activeElement.tagName)){var q=document.getElementById("q");if(q){e.preventDefault();q.focus()}}});
  window.htToast = toast; window.htPaint = paint;
  paint();
})();
