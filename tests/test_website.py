"""The website builds, every link and file it references exists, nothing a strict Content-Security-Policy would
block is in it, and a release build refuses to go out without its details. Standard library only (CI runs it
without the project's packages)."""
import importlib.util
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

WEBSITE = Path(__file__).resolve().parent.parent / "website"
spec = importlib.util.spec_from_file_location("website_build", WEBSITE / "build.py")
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)

SITE = {"site_url": "https://phantom.example", "contact_email": "hello@phantom.example", "marketplace_url": ""}


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.refs, self.ids, self.inline = [], set(), []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids.add(attrs["id"])
        for name in ("href", "src"):
            if attrs.get(name):
                self.refs.append(attrs[name])
        if tag == "style" or (tag == "script" and not attrs.get("src")) or "style" in attrs \
                or any(a.startswith("on") for a in attrs):
            self.inline.append(tag)

    def handle_data(self, data):
        if self.lasttag == "script" and data.strip():
            self.inline.append("script")


def site_file(site, folder):
    path = Path(folder) / "site.json"
    path.write_text(json.dumps(site))
    return path


class WebsiteTest(unittest.TestCase):
    def built(self, site=SITE):
        folder = tempfile.mkdtemp()
        out = Path(folder) / "dist"
        self.assertEqual(build.main(["--site", str(site_file(site, folder)), "--out", str(out)]), 0)
        return out

    def test_every_reference_exists_and_nothing_inline(self):
        out = self.built()
        for page in build.PAGES:
            text = (out / page).read_text()
            self.assertNotIn("{{", text, page)
            links = Links()
            links.feed(text)
            self.assertEqual(links.inline, [], f"{page}: inline scripts or styles break the CSP")
            for ref in links.refs:
                if ref.startswith(("https://", "mailto:")):
                    continue
                if ref.startswith("#"):
                    self.assertTrue(ref == "#" or ref[1:] in links.ids, f"{page}: no #{ref[1:]}")
                    continue
                target = ref.lstrip("/") + ("index.html" if ref.endswith("/") else "")
                self.assertTrue((out / target).is_file(), f"{page}: {ref} is missing")
        css = (out / "styles.css").read_text()
        for url in re.findall(r'url\("([^"]+)"\)', css):
            self.assertTrue((out / url).is_file(), url)
        self.assertIn("Sitemap: https://phantom.example/sitemap.xml", (out / "robots.txt").read_text())

    def test_the_main_button_follows_the_details(self):
        index = (self.built() / "index.html").read_text()
        self.assertIn('<a class="button primary" href="signin.html">Get Phantom for VS Code</a>', index)
        listing = "https://marketplace.visualstudio.com/items?itemName=phantom.phantom"
        index = (self.built({**SITE, "marketplace_url": listing}) / "index.html").read_text()
        self.assertIn("Install for VS Code", index)
        self.assertIn(f'href="{listing}"', index)

    def test_the_account_page_leads_into_vs_code(self):
        folder = Path(tempfile.mkdtemp())
        package = folder / "phantom-9.8.7.vsix"
        package.write_bytes(b"PK")
        out = folder / "dist"
        self.assertEqual(build.main(["--site", str(site_file(SITE, folder)), "--out", str(out), "--vsix", str(package)]), 0)
        account = (out / "account.html").read_text()
        self.assertIn('href="download/phantom-9.8.7.vsix" download', account)
        self.assertEqual((out / "download" / "phantom-9.8.7.vsix").read_bytes(), b"PK")
        self.assertIn("code --install-extension phantom-9.8.7.vsix", account)
        self.assertIn("Phantom: Set Up", account)
        self.assertIn("the download appears here shortly", build.install_steps(SITE, None))
        listing = "https://marketplace.visualstudio.com/items?itemName=phantom.phantom"
        self.assertIn(f'href="{listing}"', build.install_steps({**SITE, "marketplace_url": listing}, package))

    def test_a_recording_replaces_the_illustration(self):
        index = (self.built() / "index.html").read_text()
        self.assertIn('class="shot rise"', index)
        self.assertNotIn("<video", index)
        folder = Path(tempfile.mkdtemp())
        video = folder / "recording.mp4"
        video.write_bytes(b"\x00\x00\x00\x18ftypmp42")
        out = folder / "dist"
        self.assertEqual(build.main(["--site", str(site_file(SITE, folder)), "--out", str(out), "--demo", str(video)]), 0)
        index = (out / "index.html").read_text()
        self.assertIn('<video src="assets/demo.mp4" autoplay muted loop playsinline', index)
        self.assertNotIn('class="shot rise"', index)
        self.assertNotIn("<!-- demo", index)
        self.assertEqual((out / "assets" / "demo.mp4").read_bytes(), video.read_bytes())

    def test_sign_in_is_off_until_cognito_is_set_up(self):
        signin = (self.built() / "signin.html").read_text()
        self.assertIn('<meta name="phantom-auth" content="">', signin)
        self.assertIn('<span class="button google" aria-disabled="true">', signin)
        live = {**SITE, "auth_domain": "phantom-ai.auth.us-east-1.amazoncognito.com", "auth_client_id": "abc123"}
        signin = (self.built(live) / "signin.html").read_text()
        self.assertIn('content="phantom-ai.auth.us-east-1.amazoncognito.com|abc123"', signin)
        self.assertIn('data-auth="signin"', signin)
        self.assertEqual(build.problems({**live, "auth_client_id": ""}, False),
                         ["auth_domain and auth_client_id go together: fill in both, or neither"])

    @unittest.skipUnless(shutil.which("node"), "needs Node.js")
    def test_pkce_matches_rfc_7636(self):
        script = (WEBSITE / "src" / "assets" / "auth.js").read_text()
        body = script[script.index("  const base64url"):script.index("  const callback")]
        check = ("(async () => {" + body + "console.log(await challengeFor("
                 "'dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk'), randomString().length); })();")
        out = subprocess.run(["node", "-e", check], capture_output=True, text=True, check=True).stdout.split()
        self.assertEqual(out, ["E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM", "64"])

    def test_a_release_needs_every_detail(self):
        folder = tempfile.mkdtemp()
        empty = site_file({"site_url": "", "contact_email": "", "marketplace_url": ""}, folder)
        self.assertEqual(build.main(["--release", "--site", str(empty), "--out", str(Path(folder) / "d")]), 1)
        self.assertEqual(build.problems({**SITE, "site_url": "http://phantom.example/"}, False),
                         ["site_url must look like https://phantom.example (https, no path, no trailing slash)"])
        self.assertEqual(build.problems(SITE, True), [])


if __name__ == "__main__":
    unittest.main()
