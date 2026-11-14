/* Ask Claude: a slide-out panel on every tab that answers questions about what is on screen.
   The user's own Anthropic API key is kept only in this browser (localStorage) and is sent only
   to api.anthropic.com. Nothing about the key lives in the repository. */
(function () {
  "use strict";
  const SDK_URL = "assets/anthropic-sdk-1.3.0.js"; // @anthropic-ai/sdk 1.3.0, bundled for the browser
  const KEY = "his.anthropicKey", MODEL = "his.claudeModel";
  const MODELS = [
    ["claude-opus-5-5", "Claude Opus 5.5 (best answers)"],
    ["claude-sonnet-5-5", "Claude Sonnet 5.5 (faster, cheaper)"],
    ["claude-haiku-5-5", "Claude Haiku 5.5 (quickest, cheapest)"],
  ];
  const store = {
    get(k) { try { return localStorage.getItem(k) || ""; } catch (e) { return ""; } },
    set(k, v) { try { v ? localStorage.setItem(k, v) : localStorage.removeItem(k); return true; } catch (e) { return false; } },
  };
  let memKey = ""; // used when localStorage is blocked (private mode)
  const getKey = () => store.get(KEY) || memKey;
  const getModel = () => store.get(MODEL) || MODELS[0][0];

  const SYSTEM = `You are the research assistant built into Himanshu's personal "Nifty 500 Research Desk" web app. Each question arrives with a snapshot of the screen they are looking at: the tab, the controls they have set, the visible text, structured data behind the view, and sometimes images of the charts.

How the app works, so you can explain it:
- Coffee Can (Saurabh Mukherjea) is the primary filter: revenue growth above 10% and ROCE above 15% in every one of the last 10 years (ROE for lenders). With fewer years of data it reads "passes N years".
- Buffett-style checks: durable ROE above 15%, debt/equity below 0.5, stable margins, positive free cash flow, cash conversion, and the retained-earnings test.
- Damodaran-style reverse DCF: a two-stage FCFE model where growth is paid for by reinvestment (growth / ROE). Price is solved backwards for the growth it implies. Cost of equity = 6.5% risk-free + beta (clamped 0.6 to 1.6) x 7.2% equity risk premium; terminal growth 5.5%. Bear and bull values use expected growth -4 and +4 points.
- Relative value: PE against the company's own history, and a sector regression of log PE on ROCE/ROE and 3-year revenue CAGR that gives a "justified PE".
- Verdict: DCF gap (weight 0.45), peer premium (0.35), own-history percentile (up to 0.20, scaled by years of history). Score above +0.35 reads "Looks expensive", below -0.35 "Looks cheap". Guard-rails: price above 1.5x the bull value is always expensive; below two-thirds of the bear value is always cheap.
- Price chart levels come from Himanshu's LevelIndicator. Zones are bands where at least 2 pivots cluster within one band-ATR, shown only when price is near them (score = 1 - exp(-touches/7.163)). Swing levels are pivots that held for the confirmation bars; "broken" means price later traded through. "Daily-tuned" settings use 250 sessions, 10-bar pivots, zones within 3 daily ATRs; "Your settings" are the original intraday values (100 sessions, 72-bar pivots) run on daily candles.
- Data: NSE bhavcopy and RSS, Yahoo Finance (about 4 years of fundamentals, some known quirks), and Screener.in exports Himanshu adds himself.

How to answer:
- Ground every claim in the snapshot. Quote the actual numbers you see. If something is not in the snapshot, say so instead of guessing, and never invent figures.
- If a number looks implausible (for example a bank with 9% ROE from Yahoo, or a PE that jumps), point it out as a possible data error.
- Be direct and brief: the reader is often on a phone. Lead with the answer in one or two sentences, then a few short bullets if useful. Use Indian conventions (₹, crore, lakh crore).
- This is a research aid, not investment advice. You can explain what the numbers suggest and what would change the picture, but do not tell him to buy or sell; mention that once only if he asks for a recommendation.`;

  const SUGGEST = {
    screen: ["What stands out on this screen?", "Which of these pass Coffee Can and still look cheap?", "Explain the columns"],
    company: ["Why does it get this verdict?", "Explain the levels on the price chart", "What would make it fair value?", "Summarise the quality checks"],
    sector: ["Who is the best business in this sector?", "What does the market reward here?", "Which peer looks mispriced?"],
    factors: ["What does this scatter show?", "Which dots are outliers worth a look?", "Is the market paying for quality here?"],
    news: ["Which announcements matter most?", "Any results due soon?"],
    method: ["Explain the reverse DCF simply", "What are the weak spots of this method?"],
  };

  /* ---------- styles ---------- */
  const st = document.createElement("style");
  st.textContent = `
  .ac-fab{position:fixed;right:0;top:50%;transform:translateY(-50%);z-index:60;display:flex;align-items:center;gap:6px;
    writing-mode:vertical-rl;padding:14px 8px;border:0;border-radius:10px 0 0 10px;background:var(--accent);color:var(--accent-ink);
    font:600 13px/1 var(--f-body);letter-spacing:.02em;cursor:pointer;box-shadow:0 2px 10px rgba(0,0,0,.18)}
  .ac-fab svg{width:16px;height:16px;flex:none}
  .ac-fab:focus-visible{outline:2px solid var(--ink);outline-offset:2px}
  .ac-panel{position:fixed;top:0;right:0;bottom:0;z-index:70;width:min(440px,100vw);display:flex;flex-direction:column;
    background:var(--panel);color:var(--ink);border-left:1px solid var(--line);box-shadow:-8px 0 24px rgba(0,0,0,.18);
    transform:translateX(105%);transition:transform .22s ease;visibility:hidden}
  .ac-panel.open{transform:none;visibility:visible}
  .ac-head{display:flex;align-items:center;gap:8px;padding:12px 14px;border-bottom:1px solid var(--line);padding-top:max(12px,env(safe-area-inset-top))}
  .ac-head b{font:600 15px/1.2 var(--f-display);flex:1}
  .ac-head button{background:none;border:1px solid var(--line);color:var(--ink);border-radius:6px;padding:4px 8px;cursor:pointer;font-size:12px}
  .ac-ctx{font-size:12px;color:var(--muted);padding:6px 14px;border-bottom:1px solid var(--line);background:var(--soft)}
  .ac-log{flex:1;overflow-y:auto;padding:12px 14px;display:flex;flex-direction:column;gap:10px;overscroll-behavior:contain}
  .ac-m{max-width:100%;overflow-wrap:anywhere;font-size:14px;line-height:1.5}
  .ac-u{align-self:flex-end;background:var(--accent);color:var(--accent-ink);padding:7px 11px;border-radius:12px 12px 2px 12px;max-width:85%}
  .ac-a p{margin:0 0 8px}.ac-a ul,.ac-a ol{margin:0 0 8px;padding-left:20px}.ac-a h4{margin:8px 0 4px;font-size:14px}
  .ac-a code{font-family:var(--f-mono);font-size:12px;background:var(--soft);padding:1px 4px;border-radius:4px}
  .ac-a table{border-collapse:collapse;font-size:12px;margin:0 0 8px;display:block;overflow-x:auto}
  .ac-a th,.ac-a td{border:1px solid var(--line);padding:3px 6px;text-align:left}
  .ac-err{color:var(--bad);font-size:13px}
  .ac-note{font-size:12px;color:var(--muted)}
  .ac-chips{display:flex;flex-wrap:wrap;gap:6px}
  .ac-chips button{border:1px solid var(--line);background:var(--soft);color:var(--ink);border-radius:999px;padding:5px 10px;font-size:12px;cursor:pointer;text-align:left}
  .ac-form{display:flex;gap:8px;padding:10px 14px;border-top:1px solid var(--line);padding-bottom:max(10px,env(safe-area-inset-bottom))}
  .ac-form textarea{flex:1;resize:none;min-height:40px;max-height:120px;border:1px solid var(--line);border-radius:8px;padding:8px 10px;
    background:var(--bg);color:var(--ink);font:16px/1.35 var(--f-body)}
  .ac-form button{border:0;border-radius:8px;background:var(--accent);color:var(--accent-ink);padding:0 14px;font-weight:600;cursor:pointer}
  .ac-form button:disabled{opacity:.5;cursor:default}
  .ac-set{padding:14px;display:flex;flex-direction:column;gap:10px;border-bottom:1px solid var(--line)}
  .ac-set input,.ac-set select{width:100%;border:1px solid var(--line);border-radius:6px;padding:8px;background:var(--bg);color:var(--ink);font:16px var(--f-body)}
  .ac-set .row2{display:flex;gap:8px}.ac-set .row2 button{flex:1;border:1px solid var(--line);border-radius:6px;padding:8px;cursor:pointer;background:var(--soft);color:var(--ink)}
  .ac-set .row2 button.pri{background:var(--accent);color:var(--accent-ink);border-color:var(--accent)}
  .ac-dot{display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--muted);animation:acb 1s infinite alternate}
  @keyframes acb{to{opacity:.2}}
  @media (max-width:640px){
    .ac-fab{top:auto;bottom:max(16px,env(safe-area-inset-bottom));right:12px;transform:none;writing-mode:horizontal-tb;border-radius:999px;padding:11px 14px}
  }
  @media print{.ac-fab,.ac-panel{display:none}}`;
  document.head.appendChild(st);

  /* ---------- DOM ---------- */
  const spark = `<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 2l1.9 6.1L20 10l-6.1 1.9L12 18l-1.9-6.1L4 10l6.1-1.9z"/><path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"/></svg>`;
  const fab = document.createElement("button");
  fab.className = "ac-fab"; fab.type = "button"; fab.innerHTML = spark + "<span>Ask Claude</span>";
  fab.setAttribute("aria-label", "Ask Claude about this screen");
  const panel = document.createElement("aside");
  panel.className = "ac-panel"; panel.setAttribute("aria-label", "Ask Claude"); panel.setAttribute("aria-hidden", "true");
  panel.innerHTML = `
    <div class="ac-head"><b>Ask Claude</b><button type="button" data-a="new">New chat</button><button type="button" data-a="set" aria-label="Settings">Key</button><button type="button" data-a="close" aria-label="Close">✕</button></div>
    <div class="ac-set" hidden>
      <label class="ac-note" for="ac-key">Your Anthropic API key. It stays in this browser only and is sent only to api.anthropic.com. Use a key with a spending limit.</label>
      <input id="ac-key" type="password" autocomplete="off" spellcheck="false" placeholder="sk-ant-…">
      <label class="ac-note" for="ac-model">Model</label>
      <select id="ac-model">${MODELS.map(([v, l]) => `<option value="${v}">${l}</option>`).join("")}</select>
      <div class="row2"><button type="button" data-a="forget">Forget key</button><button type="button" class="pri" data-a="save">Save</button></div>
      <p class="ac-note" style="margin:0">Get a key at console.anthropic.com → API keys. Each question costs a little, billed to your Anthropic account.</p>
    </div>
    <div class="ac-ctx"></div>
    <div class="ac-log" aria-live="polite"></div>
    <form class="ac-form"><textarea rows="1" placeholder="Ask about what's on screen…" aria-label="Your question"></textarea><button type="submit">Ask</button></form>`;
  document.body.append(fab, panel);
  const q = s => panel.querySelector(s);
  const log = q(".ac-log"), ta = q("textarea"), sendBtn = q(".ac-form button"), setBox = q(".ac-set"), ctxBar = q(".ac-ctx");

  /* ---------- helpers ---------- */
  const escH = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  function md(src) { // small, escape-first markdown renderer
    const inline = s => escH(s).replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>").replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<i>$2</i>");
    const lines = String(src).replace(/\r/g, "").split("\n"); let out = "", list = null, para = [];
    const flushP = () => { if (para.length) { out += "<p>" + para.map(inline).join("<br>") + "</p>"; para = []; } };
    const flushL = () => { if (list) { out += `</${list}>`; list = null; } };
    for (let i = 0; i < lines.length; i++) {
      const l = lines[i];
      if (/^\s*\|.*\|\s*$/.test(l) && i + 1 < lines.length && /^\s*\|[\s:|-]+\|\s*$/.test(lines[i + 1])) {
        flushP(); flushL(); const cells = r => r.trim().replace(/^\||\|$/g, "").split("|").map(c => inline(c.trim()));
        out += "<table><tr>" + cells(l).map(c => `<th>${c}</th>`).join("") + "</tr>"; i++;
        while (i + 1 < lines.length && /^\s*\|.*\|\s*$/.test(lines[i + 1])) { i++; out += "<tr>" + cells(lines[i]).map(c => `<td>${c}</td>`).join("") + "</tr>"; }
        out += "</table>"; continue;
      }
      let m;
      if ((m = l.match(/^#{1,6}\s+(.*)/))) { flushP(); flushL(); out += `<h4>${inline(m[1])}</h4>`; continue; }
      if ((m = l.match(/^\s*[-*•]\s+(.*)/))) { flushP(); if (list !== "ul") { flushL(); out += "<ul>"; list = "ul"; } out += `<li>${inline(m[1])}</li>`; continue; }
      if ((m = l.match(/^\s*\d+[.)]\s+(.*)/))) { flushP(); if (list !== "ol") { flushL(); out += "<ol>"; list = "ol"; } out += `<li>${inline(m[1])}</li>`; continue; }
      if (!l.trim()) { flushP(); flushL(); continue; }
      flushL(); para.push(l);
    }
    flushP(); flushL(); return out;
  }
  const tabKey = () => { const h = decodeURIComponent(location.hash.slice(1)) || "screen"; return (h.match(/^(screen|company|sector|factors|news|method)/) || [, "screen"])[1]; };

  /* ---------- screen snapshot ---------- */
  function cardTitle(el) { const c = el.closest(".card"); const h = c && c.querySelector("h1,h2,h3"); return h ? h.textContent.trim() : ""; }
  function visibleText(view) {
    const sels = [...view.querySelectorAll("select")], prev = sels.map(s => s.style.display);
    sels.forEach(s => (s.style.display = "none"));
    let t = view.innerText || "";
    sels.forEach((s, i) => (s.style.display = prev[i]));
    t = t.replace(/[ \t]+\n/g, "\n").replace(/\n{3,}/g, "\n\n").trim();
    return t.length > 40000 ? t.slice(0, 40000) + "\n…(screen text cut at 40,000 characters)" : t;
  }
  function controls(view) {
    const out = [];
    view.querySelectorAll("select").forEach(s => {
      const lab = s.closest("label"); const name = (lab && lab.firstChild && lab.firstChild.textContent.trim()) || s.getAttribute("aria-label") || cardTitle(s) || s.id;
      out.push(`${name}: ${s.selectedOptions[0] ? s.selectedOptions[0].text : ""}`);
    });
    view.querySelectorAll("input[type=checkbox]").forEach(c => { const lab = c.closest("label"); out.push(`${lab ? lab.textContent.trim() : c.id}: ${c.checked ? "on" : "off"}`); });
    view.querySelectorAll("button.on").forEach(b => out.push(`${cardTitle(b) || "Toggle"}: ${b.textContent.trim()}`));
    return out;
  }
  function inViewport(view) {
    const H = window.innerHeight; const seen = [];
    view.querySelectorAll("h1,h2,h3").forEach(h => { const r = h.closest(".card, div").getBoundingClientRect(); if (r.bottom > 0 && r.top < H) seen.push(h.textContent.trim()); });
    return seen;
  }
  async function structured(tab) {
    try {
      if (tab === "company" && S.lastCompany && S.cache[S.lastCompany]) {
        const c = await S.cache[S.lastCompany]; const o = {};
        for (const [k, v] of Object.entries(c)) if (k !== "price" && k !== "val") o[k] = v;
        const p = c.price || {}, n = (p.t || []).length, from = Math.max(0, n - 60);
        o.last_60_daily_candles = (p.t || []).slice(from).map((t, i) => [t, p.o?.[from + i], p.h?.[from + i], p.l?.[from + i], p.c?.[from + i]]);
        o.last_60_daily_candles_columns = ["date", "open", "high", "low", "close"];
        const v = c.val || {}, m = (v.t || []).length - 1;
        if (m >= 0) o.valuation_latest = Object.fromEntries(Object.keys(v).map(k => [k, v[k][m]]));
        const lv = document.getElementById("lvset"); if (lv) o.levels_shown_on_chart = lv.value;
        return o;
      }
      if (tab === "factors") {
        const xk = document.getElementById("fx")?.value, yk = document.getElementById("fy")?.value, ind = document.getElementById("fi")?.value;
        if (!xk || !yk) return null;
        const pts = S.summary.filter(r => (!ind || r.industry === ind) && r[xk] != null && r[yk] != null)
          .map(r => [r.symbol, r.industry, +Number(r[xk]).toPrecision(4), +Number(r[yk]).toPrecision(4), r.verdict, r.coffee_can, Math.round(r.mcap || 0)]);
        return { x: FACT[xk]?.l, y: FACT[yk]?.l, sector_filter: ind || "All sectors", columns: ["symbol", "industry", "x", "y", "verdict", "coffee_can", "mcap_cr"], points: pts };
      }
      if (tab === "sector" && S.lastSector) return { sector: S.lastSector };
    } catch (e) { /* the text snapshot still goes */ }
    return null;
  }
  function chartImages(tab) { // what the canvases show, as small JPEGs
    const out = [], bg = getComputedStyle(document.body).backgroundColor || "#fff";
    const push = (canvas, label) => {
      if (!canvas || !canvas.width || !canvas.height || out.length >= 3) return;
      const scale = Math.min(1, 1100 / canvas.width), c = document.createElement("canvas");
      c.width = Math.round(canvas.width * scale); c.height = Math.round(canvas.height * scale);
      const g = c.getContext("2d"); g.fillStyle = bg; g.fillRect(0, 0, c.width, c.height); g.drawImage(canvas, 0, 0, c.width, c.height);
      out.push({ label, data: c.toDataURL("image/jpeg", 0.8).split(",")[1] });
    };
    try {
      (S.charts || []).forEach(ch => {
        const el = ch.chartElement && ch.chartElement(); if (!el) return; const r = el.getBoundingClientRect();
        if (r.width < 50 || r.bottom < 0 || r.top > window.innerHeight * 1.5) return;
        push(ch.takeScreenshot(), cardTitle(el) || "Chart");
      });
      if (tab === "factors") push(document.getElementById("cv"), "Factor scatter");
    } catch (e) { /* images are optional */ }
    return out;
  }
  async function snapshot() {
    const view = document.getElementById("view"), tab = tabKey();
    const tabName = (document.querySelector("#tabs a.on") || {}).textContent || tab;
    const parts = [`Tab: ${tabName}`, `Address: ${location.hash || "#screen"}`, `Data: ${(document.getElementById("asof") || {}).textContent || ""}`];
    const ctl = controls(view); if (ctl.length) parts.push("Controls set: " + ctl.join(" · "));
    const vp = inViewport(view); if (vp.length) parts.push("Sections in view right now: " + vp.join(" · "));
    const tip = document.getElementById("tip"); if (tip && tip.offsetParent && tip.textContent.trim()) parts.push("Tooltip showing: " + tip.textContent.trim());
    parts.push("\nVisible text:\n" + visibleText(view));
    const data = await structured(tab);
    if (data) parts.push("\nData behind this view (JSON):\n" + JSON.stringify(data));
    return { tab, label: tabName + (tab === "company" && S.lastCompany ? " · " + S.lastCompany : tab === "sector" && S.lastSector ? " · " + S.lastSector : ""), text: parts.join("\n"), images: chartImages(tab) };
  }

  /* ---------- conversation ---------- */
  let history = [], lastSnapText = "", busy = false, client = null, clientKey = "";
  function add(cls, html) { const d = document.createElement("div"); d.className = "ac-m " + cls; d.innerHTML = html; log.appendChild(d); log.scrollTop = log.scrollHeight; return d; }
  function showChips() {
    const old = log.querySelector(".ac-chips"); if (old) old.remove();
    if (history.length) return;
    const box = add("ac-chips", (SUGGEST[tabKey()] || []).map(s => `<button type="button">${escH(s)}</button>`).join(""));
    box.onclick = e => { const b = e.target.closest("button"); if (b) ask(b.textContent); };
  }
  function refreshCtx() {
    const t = tabKey(); const extra = t === "company" && S.lastCompany ? " · " + S.lastCompany : t === "sector" && S.lastSector ? " · " + S.lastSector : "";
    ctxBar.textContent = "Looking at: " + ((document.querySelector("#tabs a.on") || {}).textContent || t) + extra + ". Each question sends this screen to Claude.";
  }
  function openSettings(show) { setBox.hidden = !show; if (show) { q("#ac-key").value = getKey(); q("#ac-model").value = getModel(); q("#ac-key").focus(); } }
  async function getClient() {
    const key = getKey();
    if (client && clientKey === key) return client;
    const mod = await import(new URL(SDK_URL, document.baseURI).href);
    const Anthropic = mod.default;
    client = new Anthropic({ apiKey: key, dangerouslyAllowBrowser: true, maxRetries: 2 }); clientKey = key;
    client._A = Anthropic; return client;
  }
  function errText(e, A) {
    if (A && e instanceof A.AuthenticationError) return "Anthropic rejected the API key. Tap Key to check it.";
    if (A && e instanceof A.PermissionDeniedError) return "This key is not allowed to use that model. Try another model under Key.";
    if (A && e instanceof A.RateLimitError) return "Rate limit reached. Wait a minute and ask again.";
    if (A && e instanceof A.APIConnectionError) return "Could not reach Anthropic. Check your connection.";
    if (A && e instanceof A.APIError) return `Anthropic API error ${e.status || ""}: ${e.message}`;
    return "Something went wrong: " + (e && e.message ? e.message : e);
  }
  async function ask(question) {
    question = String(question || "").trim(); if (!question || busy) return;
    if (!getKey()) { openSettings(true); add("ac-note", "Add your Anthropic API key first, then ask again."); ta.value = question; return; }
    busy = true; sendBtn.disabled = true; ta.value = ""; ta.style.height = "";
    const chips = log.querySelector(".ac-chips"); if (chips) chips.remove();
    add("ac-u", escH(question));
    const out = add("ac-a", '<span class="ac-dot"></span> <span class="ac-note">Reading the screen…</span>');
    let A = null, turnAdded = false;
    try {
      const snap = await snapshot();
      const content = [];
      if (snap.text !== lastSnapText) {
        snap.images.forEach(im => { content.push({ type: "text", text: `Image of the "${im.label}" chart as it appears on screen:` }); content.push({ type: "image", source: { type: "base64", media_type: "image/jpeg", data: im.data } }); });
        content.push({ type: "text", text: "<screen>\n" + snap.text + "\n</screen>" });
        lastSnapText = snap.text;
      } else content.push({ type: "text", text: "(Screen unchanged since the last question.)" });
      content.push({ type: "text", text: question });
      history.push({ role: "user", content }); turnAdded = true;
      const cl = await getClient(); A = cl._A;
      const model = getModel();
      const params = { model, max_tokens: 16000, system: SYSTEM, messages: history, output_config: { effort: "medium" }, cache_control: { type: "ephemeral" } };
      if (model !== "claude-haiku-5-5") { params.betas = ["server-side-fallback-2026-07-01"]; params.fallbacks = "default"; }
      const stream = cl.beta.messages.stream(params);
      let text = "";
      for await (const ev of stream) {
        if (ev.type === "content_block_delta" && ev.delta.type === "text_delta") { text += ev.delta.text; out.innerHTML = md(text); log.scrollTop = log.scrollHeight; }
      }
      const msg = await stream.finalMessage();
      if (msg.stop_reason === "refusal") { out.innerHTML = md(text) + '<p class="ac-err">Claude declined to answer this one.</p>'; history.pop(); }
      else {
        history.push({ role: "assistant", content: msg.content.filter(b => b.type !== "fallback") });
        if (msg.stop_reason === "max_tokens") out.insertAdjacentHTML("beforeend", '<p class="ac-note">(Answer was cut short.)</p>');
        if (!text) out.innerHTML = '<p class="ac-note">No answer came back.</p>';
      }
    } catch (e) {
      if (turnAdded) history.pop(); lastSnapText = "";
      out.innerHTML = `<p class="ac-err">${escH(errText(e, A))}</p>`;
      if (A && e instanceof A.AuthenticationError) openSettings(true);
    } finally { busy = false; sendBtn.disabled = false; }
  }

  /* ---------- events ---------- */
  function open() { refreshCtx(); panel.classList.add("open"); panel.setAttribute("aria-hidden", "false"); fab.hidden = true; showChips(); if (!getKey()) openSettings(true); else if (matchMedia("(min-width:641px)").matches) ta.focus(); }
  function close() { panel.classList.remove("open"); panel.setAttribute("aria-hidden", "true"); fab.hidden = false; fab.focus(); }
  fab.onclick = open;
  panel.addEventListener("click", e => {
    const a = e.target.closest("[data-a]"); if (!a) return;
    const act = a.dataset.a;
    if (act === "close") close();
    else if (act === "set") openSettings(setBox.hidden);
    else if (act === "new") { history = []; lastSnapText = ""; log.innerHTML = ""; showChips(); }
    else if (act === "save") {
      const k = q("#ac-key").value.trim();
      if (k && !store.set(KEY, k)) memKey = k; else memKey = "";
      store.set(MODEL, q("#ac-model").value); client = null;
      openSettings(false); add("ac-note", k ? "Key saved on this device." : "No key saved.");
      if (ta.value.trim()) ask(ta.value);
    } else if (act === "forget") { store.set(KEY, ""); memKey = ""; client = null; q("#ac-key").value = ""; add("ac-note", "Key removed from this device."); }
  });
  q(".ac-form").addEventListener("submit", e => { e.preventDefault(); ask(ta.value); });
  ta.addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(ta.value); } });
  ta.addEventListener("input", () => { ta.style.height = "auto"; ta.style.height = Math.min(ta.scrollHeight, 120) + "px"; });
  document.addEventListener("keydown", e => { if (e.key === "Escape" && panel.classList.contains("open")) close(); });
  window.addEventListener("hashchange", () => { if (panel.classList.contains("open")) { refreshCtx(); setTimeout(showChips, 0); } });
})();
