# Set up once

Phantom needs **Docker** (for its sandboxes) and **Python 3** on this machine.

**Set Up** creates Phantom's own environment, then prepares one locked sandbox per language: Python,
JavaScript, TypeScript, Go, Java, C, C++, Rust, Ruby and PHP. The first time takes a few minutes.

It then asks for an optional AI key. Without one, Phantom's own engine still finds and proves bugs; with
one, its AI also suggests extra inputs to try. Its suggestions only count if they fail in the sandbox too.
