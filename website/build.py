"""Build the Phantom website: website/src -> website/dist, with the details from website/site.json.

    python website/build.py              # a preview build (empty details are left out)
    python website/build.py --release    # what the deploy runs: refuses to build without every detail

site.json:
    site_url         "https://phantom.example" (no trailing slash): canonical links, the sitemap, the share image
    contact_email    the footer and the privacy page (deletion requests)
    marketplace_url  the VS Code Marketplace listing; when set, the main button becomes "Install for VS Code"
    auth_domain      the Cognito sign-in domain (stack output AuthDomain), e.g. phantom-ai.auth.us-east-1.amazoncognito.com
    auth_client_id   the Cognito app client (stack output AuthClientId)
                     Both empty: "Continue with Google" says accounts are coming, and does nothing.
Without a Marketplace listing, the main button ("Get Phantom for VS Code") leads to the sign-in page: signing in
is joining early access, and the account page offers the download at once.

A screen recording at src/assets/demo.mp4 (or --demo PATH) replaces the illustrated editor on the home page.
"""
import argparse
import html
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PAGES = ("index.html", "signin.html", "account.html", "privacy.html", "404.html")


def load(path):
    site = json.loads(Path(path).read_text())
    return {key: str(site.get(key, "")).strip()
            for key in ("site_url", "contact_email", "marketplace_url", "auth_domain", "auth_client_id")}


def problems(site, release):
    found = []
    if site["site_url"] and not re.fullmatch(r"https://[a-z0-9.-]+(\.[a-z]{2,})", site["site_url"]):
        found.append("site_url must look like https://phantom.example (https, no path, no trailing slash)")
    if site["contact_email"] and not re.fullmatch(r"[^@\s<>\"']+@[^@\s<>\"']+\.[a-z]{2,}", site["contact_email"]):
        found.append("contact_email doesn't look like an email address")
    if site["marketplace_url"] and not site["marketplace_url"].startswith("https://marketplace.visualstudio.com/"):
        found.append("marketplace_url must be a https://marketplace.visualstudio.com/ link")
    if site.get("auth_domain") and not re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+", site["auth_domain"]):
        found.append("auth_domain must be a host name like phantom-ai.auth.us-east-1.amazoncognito.com (no https://)")
    if site.get("auth_client_id") and not re.fullmatch(r"[a-z0-9]+", site["auth_client_id"]):
        found.append("auth_client_id must be the app client id (letters and digits)")
    if bool(site.get("auth_domain")) != bool(site.get("auth_client_id")):
        found.append("auth_domain and auth_client_id go together: fill in both, or neither")
    if release:
        found += [f"{key} is empty in website/site.json" for key in ("site_url", "contact_email") if not site[key]]
    return found


def values(site):
    email = site["contact_email"]
    if site["marketplace_url"]:
        cta_href, cta_label = site["marketplace_url"], "Install for VS Code"
    else:
        cta_href, cta_label = "signin.html", "Get Phantom for VS Code"
    footer = f' · <a href="mailto:{html.escape(email)}">{html.escape(email)}</a>' if email else ""
    contact = (f'write to <a href="mailto:{html.escape(email)}">{html.escape(email)}</a> from the address you signed in '
               "with. It is deleted within 30 days." if email else "use the contact address on this page.")
    google = '<img src="assets/google.svg" alt="" width="20" height="20">Continue with Google'
    signin = bool(site.get("auth_domain") and site.get("auth_client_id"))
    if signin:  # auth.js turns the button on
        button = f'<button class="button google" type="button" data-auth="signin" aria-disabled="true">{google}</button>'
        note = ('Signing in adds you to the early access list. Phantom receives only your name and email address '
                'from Google. <a href="privacy.html">Privacy</a>')
        meta = (f'<meta name="phantom-auth" content="{html.escape(site["auth_domain"])}|'
                f'{html.escape(site["auth_client_id"])}">')
    else:
        button = f'<span class="button google" aria-disabled="true">{google}</span>'
        note = "Accounts open soon. Until then, Phantom runs on your machine without one."
        meta = '<meta name="phantom-auth" content="">'
    return {"site_url": html.escape(site["site_url"]), "cta_href": html.escape(cta_href), "cta_label": cta_label,
            "contact_footer": footer, "contact_line": contact, "google_button": button, "signin_note": note,
            "auth_meta": meta}


def newest_vsix():
    """The extension package to offer for download: the newest extension/phantom-X.Y.Z.vsix, or None."""
    def version(path):
        return tuple(int(n) for n in re.findall(r"\d+", path.stem))
    found = sorted((ROOT.parent / "extension").glob("phantom-*.vsix"), key=version)
    return found[-1] if found else None


def install_steps(site, vsix):
    """The account page's steps from signing in to using Phantom in VS Code."""
    if site["marketplace_url"]:
        first = ('<li><strong>Install Phantom</strong> from the VS Code Marketplace.<br>'
                 f'<a class="button primary" href="{html.escape(site["marketplace_url"])}">Install for VS Code</a></li>')
    elif vsix:
        first = (f'<li><strong>Download Phantom</strong> for VS Code.<br><a class="button primary" '
                 f'href="download/{html.escape(vsix.name)}" download>Download {html.escape(vsix.stem)}.vsix</a></li>'
                 '<li><strong>Install it</strong>: in VS Code, Extensions &gt; &middot;&middot;&middot; &gt; Install from VSIX,'
                 f' and pick the file. Or in a terminal:'
                 f'<code class="command">code --install-extension {html.escape(vsix.name)}</code></li>')
    else:
        first = '<li><strong>Download Phantom</strong> for VS Code: the download appears here shortly.</li>'
    return ('<ol class="install">' + first + '<li><strong>Set it up once</strong>: press Ctrl+Shift+P and run '
            '<code>Phantom: Set Up</code>. It needs Docker and Python 3 on your machine.</li>'
            '<li><strong>Write code.</strong> Open any file in a supported language: Phantom checks it as you type.</li>'
            '</ol>')


DEMO_VIDEO = ('<figure class="shot demo rise" aria-label="Phantom in VS Code catching a bug, then replaying it">'
              '<video src="assets/demo.mp4" autoplay muted loop playsinline preload="auto"></video></figure>')


def build(site, out, vsix=None, demo=None):
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(ROOT / "src", out)
    demo = demo or (ROOT / "src" / "assets" / "demo.mp4")
    if demo.is_file():  # the real recording instead of the illustrated editor
        if demo.resolve() != (out / "assets" / "demo.mp4").resolve():
            shutil.copyfile(demo, out / "assets" / "demo.mp4")
        index = out / "index.html"
        index.write_text(re.sub(r"<!-- demo:.*?<!-- /demo -->", DEMO_VIDEO, index.read_text(), flags=re.S))
    if vsix:
        (out / "download").mkdir()
        shutil.copyfile(vsix, out / "download" / vsix.name)
    fill = {**values(site), "install_steps": install_steps(site, vsix)}
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
    parser.add_argument("--vsix", type=Path, help="extension package to offer (default: newest extension/*.vsix)")
    parser.add_argument("--demo", type=Path, help="screen recording for the home page (default: src/assets/demo.mp4)")
    args = parser.parse_args(argv)
    site = load(args.site)
    found = problems(site, args.release)
    if found:
        print("website/site.json:\n  " + "\n  ".join(found), file=sys.stderr)
        return 1
    vsix = args.vsix or newest_vsix()
    print(f"built {build(site, args.out, vsix, args.demo)}" + (f" (offers {vsix.name})" if vsix else " (no extension package to offer)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
