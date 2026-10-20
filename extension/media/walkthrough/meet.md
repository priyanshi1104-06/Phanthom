# Phantom is watching your code

Write code as usual. When you pause, Phantom picks the functions you changed and runs each one on inputs
designed to break it: empty lists, zero, negative numbers, empty strings, missing values.

It runs them in a locked sandbox with no network and nothing to touch on your machine, and it tells you only
about failures that happen again when replayed. **Repro or it didn't happen.**

Look for **Phantom** in the activity bar on the left: it tells you what it found in the file you're in.
