"""Wrap app/page.html into a full HTML document for GitHub Pages (dist/)."""
import shutil
from common import ROOT, SITE

dist = ROOT / "dist"
if dist.exists():
    shutil.rmtree(dist)
shutil.copytree(SITE / "data", dist / "data")
shutil.copytree(SITE / "assets", dist / "assets")
shutil.copy(SITE / "manifest.webmanifest", dist / "manifest.webmanifest")
shutil.copy(SITE / "sw.js", dist / "sw.js")
shutil.copytree(SITE / "functions", dist / "functions")
shutil.copy(SITE.parent / "config" / "setup_rules.json", dist / "data" / "setup_rules.json")
_rs = SITE.parent / "data" / "cache" / "research.json"  # weekly research result, kept in the run cache
if _rs.exists():
    shutil.copy(_rs, dist / "data" / "research.json")
_ex = SITE.parent / "data" / "cache" / "explore"  # factor explorer files (one per stock + pooled)
if _ex.exists():
    shutil.copytree(_ex, dist / "data" / "explore")  # Setups scoring defaults  # Cloudflare Pages password middleware
HEAD = ('<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
    '<link rel="icon" type="image/png" sizes="32x32" href="assets/logo-32.png?v=5">'
    '<link rel="icon" type="image/png" sizes="192x192" href="assets/logo-192.png?v=5">'
    '<link rel="apple-touch-icon" href="assets/apple-touch-icon.png?v=5">'
    '<link rel="manifest" href="manifest.webmanifest?v=5" crossorigin="use-credentials">'
    '<meta name="theme-color" content="#0b1530">'
    '<meta name="apple-mobile-web-app-capable" content="yes">'
    '<meta name="mobile-web-app-capable" content="yes">'
    '<meta name="apple-mobile-web-app-title" content="HIS Research">'
    '<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">'
    '<style>:root{padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}'
    'body{margin:0}img{max-width:100%}[hidden]{display:none!important}</style>\n')
for src, out in (("page.html", "index.html"), ("zones.html", "zones.html")):
  body = (SITE / src).read_text()
  (dist / out).write_text(HEAD
    + body
    + '\n<script>if("serviceWorker" in navigator&&location.protocol==="https:")navigator.serviceWorker.register("sw.js").catch(()=>{});</script>'
    + "\n</html>\n")
(dist / ".nojekyll").write_text("")
print("built", dist)
