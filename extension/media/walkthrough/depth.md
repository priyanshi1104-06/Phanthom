# Depth and tiers

| Layer | What it does | |
|---|---|---|
| **Specter** | checks every function you change, every time you pause: crashes, hangs | now |
| **Analyst** | checks intent: wrong answers that don't crash, callers an edit breaks | now |
| **Oracle** | writes tests, explores deeply, proves fixes | 0.3 |

**Auto** runs Specter always and the Analyst when you open or save a file (or click **Check intent** in the
Phantom panel), or when an edit is risky (payments, auth,
concurrency, migrations). **Up to Analyst** runs both on every pause. Each layer has a tier, from **Swift** to
**Deepest**: deeper thinks harder and takes longer.

The Analyst checks examples you write in comments and docstrings (`clamp(15, 0, 10) == 10`, `>>> f(2)`) with
Phantom's own engine. With an AI key, it also reads what each function is meant to do and expects results from
that, quoting the words in your code it relied on.
