/* Notes with screenshots. A floating button on every page captures what is on screen; draw on it, write a note,
   save. Notes live in this browser (IndexedDB), with a backup download. Used by index.html and zones.html.
   window.HisNotes.capture(element, {title, text}) opens the editor for one element (e.g. a call card);
   window.HisNotes.renderList(container) draws the Notes list. */
(function () {
  const DB = "his-notes", STORE = "notes";
  const H2C = "https://cdn.jsdelivr.net/npm/html2canvas-pro@1.5.11/dist/html2canvas-pro.min.js"; // supports modern CSS colours
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  let dbp;
  const db = () => dbp || (dbp = new Promise((ok, no) => {
    const r = indexedDB.open(DB, 1);
    r.onupgradeneeded = () => r.result.createObjectStore(STORE, { keyPath: "id" });
    r.onsuccess = () => ok(r.result); r.onerror = () => no(r.error);
  }));
  const tx = async (mode, fn) => { const d = await db(); return new Promise((ok, no) => { const t = d.transaction(STORE, mode), s = t.objectStore(STORE); const r = fn(s); t.oncomplete = () => ok(r && r.result); t.onerror = () => no(t.error); }); };
  const all = () => tx("readonly", s => s.getAll()).then(a => (a || []).sort((x, y) => y.created.localeCompare(x.created)));
  const put = n => tx("readwrite", s => s.put(n));
  const del = id => tx("readwrite", s => s.delete(id));
  const get = id => tx("readonly", s => s.get(id));
  let h2c;
  const loadH2C = () => h2c || (h2c = new Promise((ok, no) => { if (window.html2canvas) return ok(window.html2canvas); const s = document.createElement("script"); s.src = H2C; s.onload = () => ok(window.html2canvas); s.onerror = () => { h2c = null; no(new Error("screenshot library did not load")); }; document.head.appendChild(s); }));

  const css = `
  .hn-fab{position:fixed;left:14px;bottom:18px;z-index:90;width:48px;height:48px;border-radius:50%;border:0;background:var(--accent,#3b82f6);color:var(--accent-ink,#fff);
    box-shadow:0 4px 14px rgba(0,0,0,.3);display:flex;align-items:center;justify-content:center;cursor:pointer}
  .hn-fab svg{width:22px;height:22px}.hn-fab[hidden]{display:none}
  .hn-modal{position:fixed;inset:0;z-index:95;background:rgba(0,0,0,.55);display:flex;align-items:center;justify-content:center;padding:12px}
  .hn-box{background:var(--panel,#fff);color:var(--ink,#111);border-radius:12px;width:min(920px,100%);max-height:100%;overflow:auto;padding:14px;display:flex;flex-direction:column;gap:10px}
  .hn-box h3{margin:0;font-size:16px}.hn-row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
  .hn-box input,.hn-box textarea{width:100%;box-sizing:border-box;font:inherit;font-size:15px;padding:8px 10px;border:1px solid var(--line,#ccc);border-radius:8px;background:var(--bg,#fff);color:inherit}
  .hn-box textarea{min-height:110px;resize:vertical}
  .hn-cv{position:relative;border:1px solid var(--line,#ccc);border-radius:8px;overflow:hidden;background:var(--soft,#eee)}
  .hn-cv canvas{display:block;width:100%;height:auto;touch-action:none;cursor:crosshair}
  .hn-btn{padding:8px 14px;border-radius:8px;border:1px solid var(--line,#ccc);background:var(--panel,#fff);color:inherit;font-weight:600;cursor:pointer;min-height:38px}
  .hn-btn.pri{background:var(--accent,#3b82f6);color:var(--accent-ink,#fff);border-color:transparent}
  .hn-sw{width:26px;height:26px;border-radius:50%;border:2px solid transparent;cursor:pointer}.hn-sw.on{border-color:var(--ink,#111)}
  .hn-list{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px}
  .hn-card{background:var(--panel,#fff);border:1px solid var(--line,#ccc);border-radius:10px;overflow:hidden;display:flex;flex-direction:column}
  .hn-card img{width:100%;max-height:220px;object-fit:cover;object-position:top;cursor:zoom-in;display:block;border-bottom:1px solid var(--line,#ccc)}
  .hn-card .b{padding:10px 12px;display:flex;flex-direction:column;gap:6px}.hn-card .t{font-weight:600}.hn-card .x{white-space:pre-wrap;font-size:14px}
  .hn-card .m{font-size:12px;color:var(--muted,#666)}.hn-card .a{display:flex;gap:6px;flex-wrap:wrap}
  .hn-zoom{position:fixed;inset:0;z-index:96;background:rgba(0,0,0,.85);display:flex;align-items:center;justify-content:center;padding:10px;cursor:zoom-out}
  .hn-zoom img{max-width:100%;max-height:100%}`;
  const st = document.createElement("style"); st.textContent = css; document.head.appendChild(st);

  function pageLabel() {
    const h = decodeURIComponent(location.hash.slice(1));
    if (/zones\.html/.test(location.pathname)) return "Zones" + (h ? " · " + h : "");
    if (h.startsWith("company-")) return h.slice(8);
    const t = document.querySelector("#tabs a.on"); return t ? t.textContent.trim() : "Setups";
  }
  const pageUrl = () => location.pathname.split("/").pop() + location.hash;

  async function shoot(el) {
    const html2canvas = await loadH2C();
    const fab = document.querySelector(".hn-fab"), ac = document.querySelectorAll(".ac-fab,.ac-panel");
    if (fab) fab.style.visibility = "hidden"; ac.forEach(x => x.style.visibility = "hidden");
    try {
      const bg = getComputedStyle(document.body).backgroundColor || "#fff";
      const scale = Math.min(window.devicePixelRatio || 1, 2);
      if (el) return await html2canvas(el, { backgroundColor: bg, scale, useCORS: true, logging: false });
      const target = document.fullscreenElement || document.querySelector(".card.fs") || document.body;
      if (target !== document.body) return await html2canvas(target, { backgroundColor: bg, scale, useCORS: true, logging: false });
      return await html2canvas(document.body, { backgroundColor: bg, scale, useCORS: true, logging: false,
        x: window.scrollX, y: window.scrollY, width: window.innerWidth, height: window.innerHeight,
        windowWidth: document.documentElement.clientWidth, windowHeight: window.innerHeight });
    } finally { if (fab) fab.style.visibility = ""; ac.forEach(x => x.style.visibility = ""); }
  }

  function editor(note) {
    return new Promise(done => {
      const host = document.fullscreenElement || document.body;
      const m = document.createElement("div"); m.className = "hn-modal";
      m.innerHTML = `<div class="hn-box" role="dialog" aria-label="Note">
        <div class="hn-row" style="justify-content:space-between"><h3>${note.id ? "Edit note" : "New note"}</h3><button class="hn-btn" data-a="x" aria-label="Close">✕</button></div>
        <input data-f="title" placeholder="Title" value="${esc(note.title)}">
        ${note.img ? `<div class="hn-row"><span style="font-size:13px;opacity:.75">Draw on it:</span>${["#ef4444", "#22c55e", "#facc15", "#3b82f6", "#ffffff"].map((c, i) => `<span class="hn-sw${i ? "" : " on"}" data-c="${c}" style="background:${c}"></span>`).join("")}<button class="hn-btn" data-a="undo">Undo</button><button class="hn-btn" data-a="drop">Remove picture</button></div>
        <div class="hn-cv"><canvas></canvas></div>` : ""}
        <textarea data-f="text" placeholder="Why is this a good idea? Entry, stop, target, what to watch…">${esc(note.text)}</textarea>
        <div class="hn-row" style="justify-content:flex-end"><span style="font-size:12px;opacity:.7;margin-right:auto">${esc(note.page || "")}</span><button class="hn-btn" data-a="x">Cancel</button><button class="hn-btn pri" data-a="save">Save note</button></div></div>`;
      host.appendChild(m);
      const close = r => { m.remove(); done(r); };
      let cv = m.querySelector("canvas"), g, base, strokes = [], cur = null, color = "#ef4444";
      const redraw = () => { g.drawImage(base, 0, 0); for (const s of strokes) { g.strokeStyle = s.c; g.lineWidth = s.w; g.lineCap = g.lineJoin = "round"; g.beginPath(); s.p.forEach(([x, y], i) => i ? g.lineTo(x, y) : g.moveTo(x, y)); g.stroke(); } };
      if (cv) {
        base = new Image(); base.onload = () => { cv.width = base.naturalWidth; cv.height = base.naturalHeight; g = cv.getContext("2d"); redraw(); }; base.src = note.img;
        const pt = e => { const r = cv.getBoundingClientRect(); return [(e.clientX - r.left) * cv.width / r.width, (e.clientY - r.top) * cv.height / r.height]; };
        cv.addEventListener("pointerdown", e => { cv.setPointerCapture(e.pointerId); cur = { c: color, w: Math.max(3, cv.width / 220), p: [pt(e)] }; strokes.push(cur); });
        cv.addEventListener("pointermove", e => { if (!cur) return; cur.p.push(pt(e)); redraw(); });
        const up = () => { cur = null; }; cv.addEventListener("pointerup", up); cv.addEventListener("pointercancel", up);
        m.querySelectorAll(".hn-sw").forEach(s => s.onclick = () => { color = s.dataset.c; m.querySelectorAll(".hn-sw").forEach(x => x.classList.toggle("on", x === s)); });
      }
      m.addEventListener("click", async e => {
        const a = e.target.closest("[data-a]"); if (!a) { if (e.target === m) close(null); return; }
        const k = a.dataset.a;
        if (k === "x") return close(null);
        if (k === "undo") { strokes.pop(); if (g) redraw(); return; }
        if (k === "drop") { note.img = null; cv = null; m.querySelector(".hn-cv").remove(); a.parentElement.remove(); return; }
        if (k === "save") {
          const n = { ...note, title: m.querySelector('[data-f="title"]').value.trim() || note.title || "Note", text: m.querySelector('[data-f="text"]').value, updated: new Date().toISOString() };
          if (cv && g) n.img = cv.toDataURL("image/jpeg", 0.82);
          if (!n.id) { n.id = "n" + Date.now().toString(36) + Math.random().toString(36).slice(2, 6); n.created = n.updated; }
          try { await put(n); toast("Saved to notes"); close(n); } catch (err) { alertBox("Could not save: " + err.message); }
        }
      });
      setTimeout(() => m.querySelector('[data-f="text"]').focus(), 50);
    });
  }
  function toast(t) { const d = document.createElement("div"); d.textContent = t; d.style.cssText = "position:fixed;left:50%;bottom:80px;transform:translateX(-50%);z-index:97;background:var(--ink,#111);color:var(--panel,#fff);padding:8px 14px;border-radius:8px;font-size:14px"; (document.fullscreenElement || document.body).appendChild(d); setTimeout(() => d.remove(), 1800); }
  function alertBox(t) { toast(t); }

  async function capture(el, preset) {
    let img = null;
    try { const c = await shoot(el); img = c.toDataURL("image/jpeg", 0.85); } catch (e) { console.warn("screenshot failed", e); toast("Screenshot failed, you can still write the note"); }
    const sym = (document.querySelector("#selco b") || {}).textContent || null;
    return editor({ title: (preset && preset.title) || pageLabel() + " · " + new Date().toLocaleDateString("en-IN"), text: (preset && preset.text) || "",
      img, page: pageLabel(), url: (preset && preset.url) || pageUrl(), sym });
  }

  // floating button
  const fab = document.createElement("button"); fab.className = "hn-fab"; fab.title = "Screenshot + note"; fab.setAttribute("aria-label", "Take a screenshot and add a note");
  fab.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7h3l2-3h6l2 3h3v12H4z"/><circle cx="12" cy="13" r="3.5"/></svg>';
  fab.onclick = () => capture(null).then(n => { if (n && window.HisNotes._onSave) window.HisNotes._onSave(n); });
  const place = () => { const fs = document.fullscreenElement; (fs || document.body).appendChild(fab); };
  document.addEventListener("fullscreenchange", place);
  if (document.body) place(); else document.addEventListener("DOMContentLoaded", place);

  async function renderList(box) {
    let N = []; try { N = await all(); } catch (e) { box.innerHTML = '<div class="empty">Notes need browser storage, which is blocked here.</div>'; return; }
    box.innerHTML = `<div class="hn-row" style="justify-content:space-between;margin-bottom:10px"><h1 style="margin:0">Notes</h1><div class="hn-row">
      <input id="hn-q" placeholder="Search notes" style="max-width:220px;font:inherit;padding:7px 10px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--ink)">
      <button class="hn-btn" id="hn-new">New note</button><button class="hn-btn" id="hn-exp">Download backup</button><label class="hn-btn" style="display:inline-flex;align-items:center">Restore<input type="file" id="hn-imp" accept="application/json" hidden></label></div></div>
      <div id="hn-l"></div><p style="font-size:12px;opacity:.7">Notes are kept in this browser on this device. Download a backup to move them to another device.</p>`;
    const draw = () => {
      const q = box.querySelector("#hn-q").value.trim().toLowerCase();
      const L = N.filter(n => !q || (n.title + " " + n.text + " " + (n.page || "") + " " + (n.sym || "")).toLowerCase().includes(q));
      box.querySelector("#hn-l").innerHTML = L.length ? `<div class="hn-list">${L.map(n => `<div class="hn-card" data-id="${n.id}">${n.img ? `<img src="${n.img}" alt="Screenshot" data-a="zoom">` : ""}
        <div class="b"><div class="t">${esc(n.title)}</div>${n.text ? `<div class="x">${esc(n.text)}</div>` : ""}<div class="m">${new Date(n.created).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" })}${n.page ? " · " + esc(n.page) : ""}</div>
        <div class="a">${n.url ? `<button class="hn-btn" data-a="open">Open page</button>` : ""}<button class="hn-btn" data-a="edit">Edit</button><button class="hn-btn" data-a="del">Delete</button></div></div></div>`).join("")}</div>`
        : `<div class="empty">${N.length ? "No notes match." : "No notes yet. Tap the camera button on any page, or \"Save to notes\" on a call."}</div>`;
    };
    box.querySelector("#hn-q").oninput = draw;
    box.querySelector("#hn-new").onclick = async () => { const n = await editor({ title: "Note · " + new Date().toLocaleDateString("en-IN"), text: "", img: null, page: "", url: "" }); if (n) { N = await all(); draw(); } };
    box.querySelector("#hn-exp").onclick = async () => { const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([JSON.stringify(await all())], { type: "application/json" })); a.download = "notes-" + new Date().toISOString().slice(0, 10) + ".json"; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 3000); };
    box.querySelector("#hn-imp").onchange = async e => { const f = e.target.files[0]; if (!f) return; try { const arr = JSON.parse(await f.text()); for (const n of arr) if (n && n.id) await put(n); N = await all(); draw(); toast("Restored " + arr.length + " notes"); } catch (err) { toast("Could not read that file"); } };
    box.querySelector("#hn-l").addEventListener("click", async e => {
      const a = e.target.closest("[data-a]"); if (!a) return; const id = a.closest("[data-id]").dataset.id, n = N.find(x => x.id === id); if (!n) return;
      if (a.dataset.a === "zoom") { const z = document.createElement("div"); z.className = "hn-zoom"; z.innerHTML = `<img src="${n.img}" alt="">`; z.onclick = () => z.remove(); document.body.appendChild(z); }
      if (a.dataset.a === "open") location.href = n.url;
      if (a.dataset.a === "edit") { const r = await editor({ ...(await get(id)) }); if (r) { N = await all(); draw(); } }
      if (a.dataset.a === "del") { if (!a.dataset.sure) { a.dataset.sure = 1; a.textContent = "Tap again to delete"; setTimeout(() => { if (a.isConnected) { delete a.dataset.sure; a.textContent = "Delete"; } }, 3000); return; } await del(id); N = await all(); draw(); }
    });
    draw();
  }
  window.HisNotes = { capture, renderList, all, _onSave: null };
})();
