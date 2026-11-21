// Cloudflare Pages middleware: one password guards the whole site.
// The password lives only in Cloudflare (Pages > Settings > Variables and secrets > SITE_PASSWORD).
// A correct password sets a signed cookie for a year; changing the password signs everyone out.
const COOKIE = "his_auth";
const OPEN = [/^\/manifest\.webmanifest$/, /^\/assets\/(logo-\d+|maskable-\d+|apple-touch-icon)\.png$/];

const enc = new TextEncoder();
async function sign(key, msg) {
  const k = await crypto.subtle.importKey("raw", enc.encode(key), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const s = new Uint8Array(await crypto.subtle.sign("HMAC", k, enc.encode(msg)));
  return btoa(String.fromCharCode(...s)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
function same(a, b) {
  if (a.length !== b.length) return false;
  let d = 0;
  for (let i = 0; i < a.length; i++) d |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return d === 0;
}
function safeNext(v) {
  const s = String(v || "/");
  return s.startsWith("/") && !s.startsWith("//") ? s : "/";
}
const esc = s => String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function loginPage(next, failed) {
  const html = `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>HIS Research</title><meta name="robots" content="noindex">
<link rel="manifest" href="/manifest.webmanifest?v=5" crossorigin="use-credentials">
<link rel="apple-touch-icon" href="/assets/apple-touch-icon.png?v=5"><meta name="theme-color" content="#0b1530">
<style>
:root{--bg:#f5f7fa;--panel:#fff;--ink:#14202e;--muted:#5b6b7d;--line:#dfe5ec;--accent:#1d5fd1;--bad:#b4372d}
@media (prefers-color-scheme:dark){:root{--bg:#0e141c;--panel:#151d28;--ink:#e4eaf2;--muted:#93a3b6;--line:#253142;--accent:#6aa1ff;--bad:#ff7b70}}
*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;padding:16px}
form{width:min(360px,100%);background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:24px;display:flex;flex-direction:column;gap:12px;text-align:center}
img{width:72px;height:72px;margin:0 auto;border-radius:16px}h1{font-size:18px;margin:0}p{margin:0;color:var(--muted);font-size:13px}
input{font-size:16px;padding:10px 12px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--ink)}
button{font-size:15px;font-weight:600;padding:10px;border:0;border-radius:8px;background:var(--accent);color:#fff;cursor:pointer}
.err{color:var(--bad)}
</style></head><body>
<form method="post" action="/__login">
<img src="/assets/logo-192.png?v=5" alt="">
<h1>Nifty 500 Research Desk</h1>
<p>Private. Enter your password.</p>
${failed ? '<p class="err">That password is not right.</p>' : ""}
<input type="password" name="password" autocomplete="current-password" required autofocus aria-label="Password">
<input type="hidden" name="next" value="${esc(next)}">
<button type="submit">Open</button>
</form></body></html>`;
  return new Response(html, { status: 401, headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store", "x-robots-tag": "noindex" } });
}

export async function onRequest({ request, env, next }) {
  const url = new URL(request.url);
  const pw = env.SITE_PASSWORD;
  if (!pw) return new Response("This site is locked until SITE_PASSWORD is set in Cloudflare.", { status: 503 });
  if (OPEN.some(r => r.test(url.pathname))) return next();
  const token = await sign(pw, "his-v1");
  const m = (request.headers.get("cookie") || "").match(/(?:^|;\s*)his_auth=([^;]+)/);
  if (m && same(m[1], token)) {
    if (url.pathname === "/__login") return Response.redirect(new URL("/", url).toString(), 303);
    const res = await next();
    const out = new Response(res.body, res);
    out.headers.set("cache-control", out.headers.get("cache-control") || "private, max-age=0, must-revalidate");
    out.headers.set("x-robots-tag", "noindex");
    return out;
  }
  if (url.pathname === "/__login" && request.method === "POST") {
    const form = await request.formData();
    const given = String(form.get("password") || "");
    const to = safeNext(form.get("next"));
    if (given && same(await sign(given, "his-v1"), token)) {
      return new Response(null, { status: 303, headers: {
        location: to,
        "set-cookie": `${COOKIE}=${token}; Path=/; Max-Age=31536000; HttpOnly; Secure; SameSite=Lax`,
      } });
    }
    await new Promise(r => setTimeout(r, 800)); // slow down guessing
    return loginPage(to, true);
  }
  return loginPage(url.pathname + url.search, false);
}
