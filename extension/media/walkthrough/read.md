# Reading a finding

```
Confirmed bug (reproduced): average([]) raises ZeroDivisionError: division by zero
```

- **Red squiggle**: the function fails on an input its declared types allow.
- **Yellow squiggle**: it fails only under the assumption Phantom names, e.g. "values is an array (guessed from its name)".
- **Faint dots**: it only fails when handed null.
- **▶ Replay** above the function runs that exact input again, against your code as it is now. After a fix it tells you the bug no longer reproduces.
