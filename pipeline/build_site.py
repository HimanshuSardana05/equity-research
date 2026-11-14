"""Wrap app/page.html into a full HTML document for GitHub Pages (dist/)."""
import shutil
from common import ROOT, SITE

dist = ROOT / "dist"
if dist.exists():
    shutil.rmtree(dist)
shutil.copytree(SITE / "data", dist / "data")
body = (SITE / "page.html").read_text()
(dist / "index.html").write_text(
    '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
    '<style>:root{padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}'
    'body{margin:0}img{max-width:100%}[hidden]{display:none!important}</style>\n'
    + body + "\n</html>\n")
(dist / ".nojekyll").write_text("")
print("built", dist)
