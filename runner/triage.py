"""Specter's triage: how risky is this code, and this edit? Milliseconds, no code is run, any language.

Each function is read as words: identifiers split on _ and camelCase, plus string contents. Words
from a category's list are the evidence. The vision's escalation rule decides the level:

    low       no signal
    medium    database, memory handling, dangerous calls, data deletion   -> Analyst should look
    high      concurrency, auth, payments                                 -> Oracle should look
    critical  database migrations, or two or more of the high categories  -> Oracle, first

The edit's risk is the highest level among the functions that changed since the last look.
"""
import re

LEVELS = ("low", "medium", "high", "critical")
CATEGORIES = {  # category: (level it raises to, words, how many different words are needed)
    "payments": ("high", {"amount", "price", "balance", "payment", "payments", "pay", "charge", "refund", "invoice",
                          "currency", "cents", "wallet", "payout", "ledger", "stripe", "paypal", "discount",
                          "subtotal", "tax", "fee", "billing", "bill", "money", "transfer"}, 2),
    "auth": ("high", {"auth", "authenticate", "authorize", "login", "logout", "password", "passwd", "token", "secret",
                      "session", "permission", "permissions", "role", "roles", "admin", "jwt", "oauth", "csrf",
                      "credential", "credentials", "apikey", "otp", "hmac"}, 1),
    "concurrency": ("high", {"thread", "threads", "threading", "mutex", "lock", "rlock", "semaphore", "atomic",
                             "goroutine", "chan", "async", "await", "asyncio", "synchronized", "volatile", "executor",
                             "spawn", "condvar", "waitgroup", "pthread", "multiprocessing", "concurrent"}, 1),
    "migration": ("critical", {"migration", "migrations", "migrate", "alembic", "alter", "drop", "truncate"}, 2),
    "database": ("medium", {"sql", "query", "execute", "cursor", "commit", "rollback", "transaction", "select",
                            "insert", "update", "delete", "sqlite3", "sqlalchemy", "psycopg2", "database", "db"}, 2),
    "memory": ("medium", {"malloc", "calloc", "realloc", "free", "memcpy", "memmove", "memset", "strcpy", "strcat",
                          "sprintf", "gets", "alloca", "unsafe", "transmute", "ptr"}, 1),
    "dangerous": ("medium", {"eval", "exec", "system", "popen", "subprocess", "pickle", "unserialize", "deserialize",
                             "shell"}, 1),
    "data_loss": ("medium", {"rmtree", "unlink", "purge", "wipe", "shred"}, 1),
}
ESCALATE = {"low": None, "medium": "analyst", "high": "oracle", "critical": "oracle"}
GO_STATEMENT = re.compile(r"(^|[\s;{])go\s+[\w.]+\s*\(", re.MULTILINE)
MIGRATION_PATH = re.compile(r"(^|/)(migrations|alembic/versions)/")
SQL_SCHEMA = re.compile(r"\b(ALTER|DROP|TRUNCATE)\s+(TABLE|COLUMN|DATABASE|INDEX)\b", re.IGNORECASE)


def words_of(text):
    """Lower-case word parts of every identifier and string: 'refundAmount' -> 'refund', 'amount'."""
    words = set()
    for token in re.findall(r"[A-Za-z][A-Za-z0-9_]*", text):
        for part in re.split(r"_|(?<=[a-z0-9])(?=[A-Z])", token):
            if part:
                words.add(part.lower())
    return words


def rate_function(fn, path=""):
    """{"level", "reasons": [{"category", "evidence"}]} for one function."""
    words = words_of(fn.text)
    reasons, level = [], "low"
    for category, (raises, vocabulary, needed) in CATEGORIES.items():
        hits = sorted(words & vocabulary)
        if category == "concurrency" and GO_STATEMENT.search(fn.text):
            hits.append("go statement")
        if category == "migration":
            if MIGRATION_PATH.search(path or ""):
                hits = ["migrations folder", *hits]
                needed = 1
            if SQL_SCHEMA.search(fn.text):
                hits = ["schema change", *hits]
                needed = 1
        if len(hits) >= needed:
            reasons.append({"category": category, "evidence": hits[:4]})
            level = max(level, raises, key=LEVELS.index)
    if sum(1 for r in reasons if CATEGORIES[r["category"]][0] == "high") >= 2:
        level = "critical"
    return {"level": level, "reasons": reasons}


def triage(functions, path="", previous=None):
    """Risk of the file and of the edit. `previous` = {function name: text} from the last look,
    or None for the first look (then nothing counts as an edit, so opening a file never escalates)."""
    rated, changed = {}, []
    for fn in functions:
        rated[fn.name] = {"line": fn.line, **rate_function(fn, path)}
        if previous is not None and previous.get(fn.name) != fn.text:
            changed.append(fn.name)
    file_level = max((r["level"] for r in rated.values()), default="low", key=LEVELS.index)
    edit_level = max((rated[n]["level"] for n in changed), default="low", key=LEVELS.index)
    reasons = [{"function": name, **reason} for name in (changed or rated) for reason in rated[name]["reasons"]]
    return {
        "level": file_level,
        "edit_level": edit_level,
        "changed": changed,
        "escalate": ESCALATE[edit_level] if changed else None,
        "functions": rated,
        "reasons": reasons,
    }


def raise_level(level, model_level):
    """The model may raise a function's level by one step, never lower it."""
    if model_level not in LEVELS:
        return level
    return min(max(level, model_level, key=LEVELS.index), LEVELS[min(LEVELS.index(level) + 1, 3)],
               key=LEVELS.index)
