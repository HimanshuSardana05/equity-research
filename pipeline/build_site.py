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
shutil.copytree(SITE / "functions", dist / "functions")  # Cloudflare Pages password middleware
body = (SITE / "page.html").read_text()
(dist / "index.html").write_text(
    '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
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
    'body{margin:0}img{max-width:100%}[hidden]{display:none!important}</style>\n'
    + body
    + '\n<script>if("serviceWorker" in navigator&&location.protocol==="https:")navigator.serviceWorker.register("sw.js").catch(()=>{});</script>'
    + "\n</html>\n")
(dist / ".nojekyll").write_text("")
print("built", dist)
