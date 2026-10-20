# Depth and tiers

| Layer | What it does | |
|---|---|---|
| **Specter** | checks every function you change, every time you pause | now |
| **Analyst** | checks intent: wrong answers that don't crash, callers an edit breaks | 0.2 |
| **Oracle** | writes tests, explores deeply, proves fixes | 0.3 |

**Auto** runs Specter always and calls the deeper layers only when an edit is risky (payments, auth,
concurrency, migrations). Each layer has a tier, from **Swift** to **Deepest**: deeper thinks harder and takes longer.
