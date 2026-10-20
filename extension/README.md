# Phantom

Phantom finds bugs while you write code, before you run it, and backs every report with a failing
input that it replayed in a sandbox. **Repro or it didn't happen.** It never claims code is bug-free.

Works in Python, JavaScript, TypeScript, Go, Java, C, C++, Rust, Ruby and PHP.

## Getting started

1. Install [Docker](https://docs.docker.com/engine/install/) and Python 3.
2. Run **Phantom: Set Up** from the Command Palette (Ctrl+Shift+P). It creates Phantom's own Python
   environment, pulls one locked-down sandbox image per language and asks for an optional Anthropic API key.
3. Open a file. When you pause typing, Phantom checks the functions you changed.

## What you see

- **Red squiggle**: the function fails on an input its declared types allow, e.g. `average([])` raises.
- **Yellow squiggle**: it fails only under a named assumption (untyped code, extreme numbers).
- **Faint dots**: it fails only when handed null.
- **▶ Replay** above a function: runs that input again in a fresh sandbox against your current code.
- **Status bar**: confirmed and assumed bugs, the edit's risk (`risk high → Oracle`), and the depth and
  model picker: click it to choose the depth and the Claude model for each layer.

## The three layers

| Layer | What it does | Version |
|---|---|---|
| Specter | runs every changed function on boundary inputs and the model's suggestions, in a sandbox | 0.1 |
| Analyst | checks intent (wrong answers that don't crash) and callers an edit breaks | 0.2 |
| Oracle | writes tests, explores deep, proves fixes | 0.3 |

Every layer's suspicion must reproduce in the sandbox before you see it.

## Privacy

Your code runs only on your machine, in Docker containers with no network. With an API key, the function
being checked is sent to Anthropic's API so the model can suggest inputs.
