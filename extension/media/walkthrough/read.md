# Reading a finding

```
Confirmed bug (reproduced): average([]) raises ZeroDivisionError: division by zero
```

- **Red squiggle**: the function fails on an input its declared types allow.
- **Yellow squiggle**: it fails only under the assumption Phantom names, e.g. "values is an array (guessed from its name)".
- **Faint dots**: it only fails when handed null.

```
Wrong result (reproduced): is_leap_year(1900) returned True, expected False: the code's own example says so
```

- **Wrong result** (from the Analyst): the call runs fine but returns something else than expected. Red when your
  own example in a comment or docstring says so; yellow when the expected value is the AI's reading of what the
  function is meant to do (it quotes the words it relied on, so you can judge).
- **▶ Replay** above the function runs that exact input again, against your code as it is now. After a fix it tells you the bug no longer reproduces.
