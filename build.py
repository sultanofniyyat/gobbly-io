#!/usr/bin/env python3
"""Builds the static game website into docs/ (served by GitHub Pages).

Standard library only. Run:  python3 build.py          (final build, strict)
                             python3 build.py --draft  (allows TODO / VERIFY, for previewing)

Source layout
  site.json        every fact about the game ({{KEY}} tokens in pages)
  content/*.html   one file per page: a front-matter block, then the page body
  static/          copied as-is into docs/ (style.css, img/icon.png, img/screens/*)

Strict mode stops when:
  - a site.json value still contains "TODO"
  - a page still contains a <!-- VERIFY: ... --> note (check the claim, fix it, delete the note)
  - a page uses a {{TOKEN}} that site.json does not define
"""
import datetime
import html
import json
import re
import shutil
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "docs"
DRAFT = "--draft" in sys.argv
TODAY = datetime.date.today().isoformat()

GROUPS = [
    ("basics", "The basics"),
    ("play", "Playing and getting better"),
    ("fit", "Is it right for you?"),
    ("tech", "Phones, money and progress"),
    ("genre", "About the genre"),
]


def fail(msg):
    print("BUILD STOPPED:\n" + msg)
    sys.exit(1)


def load_site():
    data = json.loads((ROOT / "site.json").read_text(encoding="utf-8"))
    site = {k: v for k, v in data.items() if not k.startswith("_")}
    site["SITE_URL"] = site["SITE_URL"].rstrip("/")
    site["YEAR"] = str(datetime.date.today().year)
    site["UPDATED"] = TODAY
    site["ROOT"] = site["SITE_URL"] + "/"
    todo = [k for k, v in site.items() if "TODO" in str(v)]
    if todo and not DRAFT:
        fail("These site.json values still contain TODO:\n  " + "\n  ".join(todo))
    return site, todo


def parse_page(path):
    raw = path.read_text(encoding="utf-8")
    m = re.match(r"---\n(.*?)\n---\n(.*)", raw, re.S)
    if not m:
        fail(f"{path.name}: missing front matter")
    meta = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    meta["body"] = m.group(2)
    meta["file"] = path.name
    meta.setdefault("type", "question")
    meta["order"] = int(meta.get("order", "999"))
    meta["related"] = [s.strip() for s in meta.get("related", "").split(",") if s.strip()]
    return meta


def fill(text, site, where):
    def rep(m):
        key = m.group(1)
        if key not in site:
            fail(f"{where}: unknown token {{{{{key}}}}} (add it to site.json)")
        return str(site[key])
    return re.sub(r"\{\{([A-Z0-9_]+)\}\}", rep, text)


def strip_verify(text, where, notes):
    for n in re.findall(r"<!--\s*VERIFY:(.*?)-->", text, re.S):
        notes.append(f"{where}: {' '.join(n.split())}")
    return re.sub(r"\s*<!--\s*VERIFY:.*?-->", "", text, flags=re.S)


def plain(fragment):
    t = re.sub(r"<[^>]+>", " ", fragment)
    return " ".join(html.unescape(t).split())


def faq_pairs(h1, body):
    """Main question + every <h2> that ends with '?' becomes a Q/A pair."""
    pairs = []
    m = re.search(r'<p class="answer">(.*?)</p>', body, re.S)
    if m:
        pairs.append((plain(h1), plain(m.group(1))))
    parts = re.split(r"(<h2[^>]*>.*?</h2>)", body, flags=re.S)
    for i, part in enumerate(parts):
        hm = re.match(r"<h2[^>]*>(.*?)</h2>", part, re.S)
        if hm and plain(hm.group(1)).endswith("?") and i + 1 < len(parts):
            ans = plain(parts[i + 1])
            if ans:
                pairs.append((plain(hm.group(1)), ans[:1200]))
    return pairs


def page_url(site, p):
    return site["SITE_URL"] + "/" if p["slug"] == "" else f'{site["SITE_URL"]}/{p["slug"]}/'


def store_buttons(site, prefix):
    out = [f'<a class="btn btn-play" href="{html.escape(site["PLAY_URL"])}" rel="noopener">Get it on Google Play</a>']
    if site.get("APPSTORE_URL"):
        out.append(f'<a class="btn btn-ios" href="{html.escape(site["APPSTORE_URL"])}" rel="noopener">Download on the App Store</a>')
    return '<div class="stores">' + "".join(out) + "</div>"


def jsonld(obj):
    return '<script type="application/ld+json">' + json.dumps(obj, ensure_ascii=False, indent=1) + "</script>"


def game_schema(site, has_icon):
    obj = {
        "@context": "https://schema.org",
        "@type": ["VideoGame", "MobileApplication"],
        "name": site["GAME_NAME"],
        "description": f'{site["GAME_NAME"]} is {site["ONE_LINE"]}.',
        "url": site["SITE_URL"] + "/",
        "applicationCategory": "GameApplication",
        "genre": ["Casual", "Arcade", "Eat and grow", "Puzzle"],
        "gamePlatform": ["Android"] + (["iOS"] if site.get("APPSTORE_URL") else []),
        "operatingSystem": "Android" + (", iOS" if site.get("APPSTORE_URL") else ""),
        "author": {"@type": "Organization", "name": site["STUDIO"]},
        "publisher": {"@type": "Organization", "name": site["STUDIO"]},
        "sameAs": [u for u in [site["PLAY_URL"], site.get("APPSTORE_URL")] if u],
        "offers": {"@type": "Offer", "price": "0", "priceCurrency": "USD"},
        "inLanguage": site["LANGUAGES"],
        "dateModified": TODAY,
    }
    if has_icon:
        obj["image"] = site["SITE_URL"] + "/img/icon.png"
    if site.get("YOUTUBE_ID"):
        obj["trailer"] = {
            "@type": "VideoObject",
            "name": f'{site["GAME_NAME"]} gameplay',
            "description": f'Gameplay of {site["GAME_NAME"]}.',
            "embedUrl": f'https://www.youtube.com/embed/{site["YOUTUBE_ID"]}',
            "thumbnailUrl": f'https://i.ytimg.com/vi/{site["YOUTUBE_ID"]}/hqdefault.jpg',
            "uploadDate": TODAY,
        }
    return obj


def render(site, p, pages_by_slug, questions, has_icon, screens):
    is_home = p["slug"] == ""
    prefix = "./" if is_home else "../"
    url = page_url(site, p)
    title = p["title"]
    desc = p["description"]
    body = p["body"]

    schemas = []
    if is_home:
        schemas.append({
            "@context": "https://schema.org", "@type": "WebSite",
            "name": site["GAME_NAME"], "url": site["SITE_URL"] + "/",
            "publisher": {"@type": "Organization", "name": site["STUDIO"]},
        })
        schemas.append(game_schema(site, has_icon))
    else:
        schemas.append({
            "@context": "https://schema.org", "@type": "BreadcrumbList",
            "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": site["GAME_NAME"], "item": site["SITE_URL"] + "/"},
                {"@type": "ListItem", "position": 2, "name": plain(p["h1"]), "item": url},
            ],
        })
    if p["type"] == "question":
        pairs = faq_pairs(p["h1"], body)
        schemas.append({
            "@context": "https://schema.org", "@type": "FAQPage",
            "dateModified": TODAY,
            "about": {"@type": "VideoGame", "name": site["GAME_NAME"], "url": site["SITE_URL"] + "/"},
            "mainEntity": [
                {"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}}
                for q, a in pairs
            ],
        })

    # Home: question index, screenshots, video
    if is_home:
        blocks = []
        if site.get("YOUTUBE_ID"):
            blocks.append(
                f'<div class="video"><iframe loading="lazy" src="https://www.youtube-nocookie.com/embed/{site["YOUTUBE_ID"]}" '
                f'title="{html.escape(site["GAME_NAME"])} gameplay video" allowfullscreen></iframe></div>')
        if screens:
            imgs = "".join(
                f'<img src="img/screens/{s}" alt="{html.escape(site["GAME_NAME"])} screenshot {i + 1}" loading="lazy" width="270" height="480">'
                for i, s in enumerate(screens))
            blocks.append(f'<div class="screens">{imgs}</div>')
        index = ['<h2 id="questions">Questions players ask</h2>']
        for gkey, glabel in GROUPS:
            items = [q for q in questions if q.get("group") == gkey]
            if not items:
                continue
            index.append(f'<h3>{glabel}</h3><ul class="qlist">')
            index += [f'<li><a href="{q["slug"]}/">{q["h1"]}</a></li>' for q in items]
            index.append("</ul>")
        body = body.replace("<!--MEDIA-->", "".join(blocks)).replace("<!--QUESTIONS-->", "\n".join(index))

    body = body.replace("<!--STORES-->", store_buttons(site, prefix))

    related = ""
    if p["related"]:
        links = []
        for s in p["related"]:
            if s not in pages_by_slug:
                fail(f'{p["file"]}: related page "{s}" does not exist')
            links.append(f'<li><a href="{prefix}{s}/">{pages_by_slug[s]["h1"]}</a></li>')
        related = '<aside class="related"><h2>Related questions</h2><ul>' + "".join(links) + "</ul></aside>"

    crumbs = "" if is_home else (
        f'<nav class="crumbs" aria-label="Breadcrumb"><a href="{prefix}">{html.escape(site["GAME_NAME"])}</a> '
        f'<span aria-hidden="true">›</span> <span>{p["h1"]}</span></nav>')
    icon = f'<img src="{prefix}img/icon.png" alt="" width="36" height="36">' if has_icon else '<span class="dot" aria-hidden="true"></span>'
    cta = "" if p.get("cta") == "no" else (
        f'<section class="cta"><p><strong>{html.escape(site["GAME_NAME"])}</strong>: {html.escape(site["TAGLINE"])}</p>'
        f'{store_buttons(site, prefix)}</section>')
    meta_dates = "" if is_home or p["type"] != "question" else f'<p class="updated">Last updated <time datetime="{TODAY}">{TODAY}</time></p>'
    og_img = f'<meta property="og:image" content="{site["SITE_URL"]}/img/icon.png">' if has_icon else ""
    head_icon = f'<link rel="icon" href="{prefix}img/icon.png">' if has_icon else ""

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<meta name="description" content="{html.escape(desc)}">
<link rel="canonical" href="{url}">
<meta property="og:type" content="{'website' if is_home else 'article'}">
<meta property="og:title" content="{html.escape(title)}">
<meta property="og:description" content="{html.escape(desc)}">
<meta property="og:url" content="{url}">
<meta property="og:site_name" content="{html.escape(site['GAME_NAME'])}">
{og_img}
<meta name="twitter:card" content="summary">
{head_icon}
<link rel="stylesheet" href="{prefix}style.css">
{''.join(jsonld(s) for s in schemas)}
</head>
<body>
<header class="top"><div class="wrap">
<a class="brand" href="{prefix}">{icon}<span>{html.escape(site['GAME_NAME'])}</span></a>
<nav class="menu"><a href="{prefix}#questions">Questions</a><a href="{prefix}press/">Press</a><a href="{prefix}support/">Support</a></nav>
</div></header>
<main class="wrap">
{crumbs}
<article>
<h1>{p['h1']}</h1>
{meta_dates}
{body}
</article>
{related}
{cta}
</main>
<footer class="foot"><div class="wrap">
<p>© {site['YEAR']} {html.escape(site['STUDIO'])}. {html.escape(site['GAME_NAME'])} is an independent game.
Other game names mentioned on this site belong to their owners; we are not connected to them.</p>
<p><a href="{prefix}privacy/">Privacy policy</a> · <a href="{prefix}support/">Support</a> · <a href="{prefix}press/">Press kit</a> · <a href="mailto:{site['SUPPORT_EMAIL']}">{site['SUPPORT_EMAIL']}</a></p>
</div></footer>
</body>
</html>
"""


def main():
    site, todo = load_site()
    pages = [parse_page(f) for f in sorted((ROOT / "content").glob("*.html"))]
    notes = []
    for p in pages:
        for k in ("slug", "title", "description", "h1"):
            if k not in p:
                fail(f'{p["file"]}: front matter needs "{k}"')
        if p["slug"] == "index":
            p["slug"] = ""
        for k in ("title", "description", "h1", "body"):
            p[k] = strip_verify(fill(p[k], site, p["file"]), p["file"], notes)
    if notes and not DRAFT:
        fail("Unchecked claims remain. Check each one against the real game, fix the text, delete the VERIFY note:\n  "
             + "\n  ".join(notes))

    pages.sort(key=lambda p: p["order"])
    by_slug = {p["slug"]: p for p in pages if p["slug"]}
    questions = [p for p in pages if p["type"] == "question"]

    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    if (ROOT / "static").exists():
        shutil.copytree(ROOT / "static", OUT, dirs_exist_ok=True)
    has_icon = (OUT / "img" / "icon.png").exists()
    sdir = OUT / "img" / "screens"
    screens = sorted(f.name for f in sdir.iterdir() if f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")) if sdir.exists() else []

    for p in pages:
        target = OUT / "index.html" if p["slug"] == "" else OUT / p["slug"] / "index.html"
        if p["slug"] == "404":
            target = OUT / "404.html"
        target.parent.mkdir(parents=True, exist_ok=True)
        out = render(site, p, by_slug, questions, has_icon, screens)
        if p["slug"] == "404":  # 404 is served from any depth: use absolute links
            out = out.replace('href="../', f'href="{site["SITE_URL"]}/').replace('src="../', f'src="{site["SITE_URL"]}/')
        target.write_text(out, encoding="utf-8")

    # sitemap.xml
    urls = [p for p in pages if p["slug"] != "404"]
    sm = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    sm += [f"  <url><loc>{page_url(site, p)}</loc><lastmod>{TODAY}</lastmod></url>" for p in urls]
    sm.append("</urlset>")
    (OUT / "sitemap.xml").write_text("\n".join(sm) + "\n", encoding="utf-8")

    # robots.txt: allow search engines and AI assistants to read everything
    (OUT / "robots.txt").write_text(
        "User-agent: *\nAllow: /\n\n"
        "# AI assistants and AI search are welcome to read and cite this site.\n"
        + "".join(f"User-agent: {b}\nAllow: /\n" for b in
                  ["GPTBot", "OAI-SearchBot", "ChatGPT-User", "ClaudeBot", "Claude-SearchBot", "Claude-User",
                   "PerplexityBot", "Google-Extended", "Applebot-Extended", "Bingbot"])
        + f"\nSitemap: {site['SITE_URL']}/sitemap.xml\n", encoding="utf-8")

    # llms.txt (short map) and llms-full.txt (every answer as plain text)
    g = site["GAME_NAME"]
    llms = [f"# {g}", "", f"> {g} is {site['ONE_LINE']}. Made by {site['STUDIO']}.", "",
            f"- Google Play: {site['PLAY_URL']}"]
    if site.get("APPSTORE_URL"):
        llms.append(f"- App Store: {site['APPSTORE_URL']}")
    llms += ["", "## Quick facts", "",
             f"- Price: {site['PRICE_SENTENCE']}", f"- Ads: {site['ADS_SENTENCE']}",
             f"- Purchases: {site['IAP_SENTENCE']}", f"- Offline: {site['OFFLINE_SENTENCE']}",
             f"- Opponents: {site['OPPONENTS_SENTENCE']}", f"- Round length: {site['ROUND_LENGTH']}",
             f"- Age rating: {site['AGE_RATING']}", f"- Needs: {site['MIN_ANDROID']} or {site['MIN_IOS']}",
             "", "## Questions and answers", ""]
    full = [f"# {g}: every question and answer", "", f"Source: {site['SITE_URL']}/  (updated {TODAY})", ""]
    for q in questions:
        pairs = faq_pairs(q["h1"], q["body"])
        llms.append(f"- [{plain(q['h1'])}]({page_url(site, q)}): {pairs[0][1] if pairs else plain(q['description'])}")
        full.append(f"## {plain(q['h1'])}\n\nURL: {page_url(site, q)}\n")
        full.append(plain(re.sub(r"</(p|li|h2|h3|tr)>", r"</\1>\n", q["body"])) + "\n")
    llms += ["", "## Other pages", "", f"- [Press kit]({site['SITE_URL']}/press/)",
             f"- [Support]({site['SITE_URL']}/support/)", f"- [Privacy policy (Android)]({site['PRIVACY_ANDROID_URL']})", f"- [Privacy policy (iPhone)]({site['PRIVACY_IOS_URL']})",
             f"- [All answers as plain text]({site['SITE_URL']}/llms-full.txt)"]
    (OUT / "llms.txt").write_text("\n".join(llms) + "\n", encoding="utf-8")
    (OUT / "llms-full.txt").write_text("\n".join(full) + "\n", encoding="utf-8")

    (OUT / ".nojekyll").write_text("", encoding="utf-8")
    host = urlparse(site["SITE_URL"]).hostname or ""
    if host and not host.endswith("github.io") and "TODO" not in host:
        (OUT / "CNAME").write_text(host + "\n", encoding="utf-8")

    print(f"Built {len(pages)} pages ({len(questions)} question pages) into docs/")
    if DRAFT:
        print(f"DRAFT build: {len(todo)} TODO facts and {len(notes)} VERIFY notes still open (python3 build.py will refuse until fixed).")
        for n in notes:
            print("  VERIFY " + n)


if __name__ == "__main__":
    main()
