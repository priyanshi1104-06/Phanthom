"""The website builds, every link and file it references exists, nothing a strict Content-Security-Policy would
block is in it, and a release build refuses to go out without its details. Standard library only (CI runs it
without the project's packages)."""
import importlib.util
import json
import re
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
        self.assertIn('href="mailto:hello@phantom.example?subject=Phantom%20early%20access"', index)
        listing = "https://marketplace.visualstudio.com/items?itemName=phantom.phantom"
        index = (self.built({**SITE, "marketplace_url": listing}) / "index.html").read_text()
        self.assertIn("Install for VS Code", index)
        self.assertIn(f'href="{listing}"', index)

    def test_a_release_needs_every_detail(self):
        folder = tempfile.mkdtemp()
        empty = site_file({"site_url": "", "contact_email": "", "marketplace_url": ""}, folder)
        self.assertEqual(build.main(["--release", "--site", str(empty), "--out", str(Path(folder) / "d")]), 1)
        self.assertEqual(build.problems({**SITE, "site_url": "http://phantom.example/"}, False),
                         ["site_url must look like https://phantom.example (https, no path, no trailing slash)"])
        self.assertEqual(build.problems(SITE, True), [])


if __name__ == "__main__":
    unittest.main()
