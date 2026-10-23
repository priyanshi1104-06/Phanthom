"""Build the Phantom website: website/src -> website/dist, with the details from website/site.json.

    python website/build.py              # a preview build (empty details are left out)
    python website/build.py --release    # what the deploy runs: refuses to build without every detail

site.json:
    site_url         "https://phantom.example" (no trailing slash): canonical links, the sitemap, the share image
    contact_email    where "Request early access" writes to, and the footer
    marketplace_url  the VS Code Marketplace listing; when set, the main button becomes "Install for VS Code"
"""
import argparse
import html
import json
import re
import shutil
import sys
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent
PAGES = ("index.html", "technical.html", "404.html")


def load(path):
    site = json.loads(Path(path).read_text())
    return {key: str(site.get(key, "")).strip() for key in ("site_url", "contact_email", "marketplace_url")}


def problems(site, release):
    found = []
    if site["site_url"] and not re.fullmatch(r"https://[a-z0-9.-]+(\.[a-z]{2,})", site["site_url"]):
        found.append("site_url must look like https://phantom.example (https, no path, no trailing slash)")
    if site["contact_email"] and not re.fullmatch(r"[^@\s<>\"']+@[^@\s<>\"']+\.[a-z]{2,}", site["contact_email"]):
        found.append("contact_email doesn't look like an email address")
    if site["marketplace_url"] and not site["marketplace_url"].startswith("https://marketplace.visualstudio.com/"):
        found.append("marketplace_url must be a https://marketplace.visualstudio.com/ link")
    if release:
        found += [f"{key} is empty in website/site.json" for key in ("site_url", "contact_email") if not site[key]]
    return found


def values(site):
    email = site["contact_email"]
    if site["marketplace_url"]:
        cta_href, cta_label = site["marketplace_url"], "Install for VS Code"
    elif email:
        cta_href, cta_label = f"mailto:{email}?subject={quote('Phantom early access')}", "Request early access"
    else:
        cta_href, cta_label = "#early-access", "Request early access"
    footer = f' · <a href="mailto:{html.escape(email)}">{html.escape(email)}</a>' if email else ""
    return {"site_url": html.escape(site["site_url"]), "cta_href": html.escape(cta_href), "cta_label": cta_label,
            "contact_footer": footer}


def build(site, out):
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(ROOT / "src", out)
    fill = values(site)
    for page in PAGES:
        text = (out / page).read_text()
        text = re.sub(r"\{\{(\w+)\}\}", lambda m: fill[m.group(1)], text)
        (out / page).write_text(text)
    robots = "User-agent: *\nAllow: /\n"
    if site["site_url"]:
        robots += f"Sitemap: {site['site_url']}/sitemap.xml\n"
        (out / "sitemap.xml").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            f"  <url><loc>{html.escape(site['site_url'])}/</loc></url>\n</urlset>\n")
    (out / "robots.txt").write_text(robots)
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--release", action="store_true", help="refuse to build without every detail")
    parser.add_argument("--site", default=ROOT / "site.json", help="details file (default website/site.json)")
    parser.add_argument("--out", default=ROOT / "dist", type=Path, help="output folder (default website/dist)")
    args = parser.parse_args(argv)
    site = load(args.site)
    found = problems(site, args.release)
    if found:
        print("website/site.json:\n  " + "\n  ".join(found), file=sys.stderr)
        return 1
    print(f"built {build(site, args.out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
