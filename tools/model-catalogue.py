"""Compare the ids this repo NAMES against the ids opencode Zen actually serves.

WHY THIS EXISTS. On 2026-09-04 the roster named `ox-alpha-free` as the only
free model safe for private work and `qwen3.7-plus` as the backup. Neither is
in the catalogue. Nothing noticed, because "is this id still real" was a
question a session had to think to ask, and a rule that depends on an agent
remembering to look is a wish (rules/shared/memory-integrity.md). This makes it
a command that resolves the answer from the system that owns it (golden rule
A5).

FAILING TO READ THE CATALOGUE IS NOT "NOTHING IS WRONG". A network error exits
2, distinct from the 0 that means every named id is real, because silence and
success must never look alike. That is the single most-broken rule in this repo
and this tool is one network call away from breaking it.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import urllib.error
import urllib.request

CATALOGUE_URL = "https://opencode.ai/zen/v1/models"
PRICING_URL = "https://opencode.ai/docs/zen#pricing"
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Prose files that carry the roster. Declared, never discovered at runtime
# (docs/scoped-by-declaration.md).
ROSTER_DOCS = (
    "docs/model-routing.md",
    "rules/shared/cost.md",
    "docs/work-routing.md",
)

# A model id: lowercase, at least one hyphen, at least one digit somewhere.
# Loose on purpose. A false positive costs one line of output; a false negative
# costs a launch that fails on an id nobody knew was dead.
ID_SHAPE = re.compile(r"^[a-z][a-z0-9.]*(?:-[a-z0-9.]+)+$")

# A backticked token ending in a file extension is a PATH, not a model. Without
# this, `n8n-deploy.py` and `operating-model-v3.md` are reported as dead ids.
NOT_AN_ID = re.compile(
    r"\.(py|md|sh|ps1|psm1|json|jsonc|csv|toon|tsv|ya?ml|txt|sql|toml|ini|log)$")

EXIT_OK, EXIT_DEAD_ID, EXIT_UNREADABLE = 0, 1, 2


def fetch_catalogue(url: str = CATALOGUE_URL, timeout: int = 30):
    """Live ids, or (None, reason). None is never an empty set."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        return None, f"{type(exc).__name__}: {exc}"
    rows = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return None, "unexpected payload shape (no list of models)"
    ids = {str(r.get("id")) for r in rows if isinstance(r, dict) and r.get("id")}
    if not ids:
        return None, "the catalogue parsed but named no models"
    return ids, ""


def strip_provider(value: str) -> str:
    """`opencode-go/gpt-5-nano` -> `gpt-5-nano`."""
    return value.split("/")[-1].strip()


def order_models(repo: str = REPO):
    """Every `model:` an order declares. These are load-bearing: a dead id here
    is a launch that fails."""
    found = {}
    pattern = os.path.join(repo, "queue", "orders", "*.md")
    for path in sorted(glob.glob(pattern)):
        rel = os.path.relpath(path, repo)
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if line.strip() in ("---", ""):
                        continue
                    m = re.match(r"^model:\s*(\S+)", line)
                    if m:
                        found.setdefault(strip_provider(m.group(1)), []).append(rel)
                        break
                    if not re.match(r"^\w+:", line):
                        break
        except OSError:
            continue
    return found


def doc_models(repo: str = REPO, docs=ROSTER_DOCS):
    """Backticked id-shaped tokens in the roster prose."""
    found = {}
    for rel in docs:
        path = os.path.join(repo, rel)
        try:
            text = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for token in re.findall(r"`([^`\n]+)`", text):
            tok = strip_provider(token)
            if (ID_SHAPE.match(tok) and any(c.isdigit() for c in tok)
                    and not NOT_AN_ID.search(tok)):
                found.setdefault(tok, set()).add(rel)
    return {k: sorted(v) for k, v in found.items()}


def report(live, ordered, documented, out=sys.stdout) -> int:
    """Print the comparison. Returns the exit code."""
    dead_orders = {k: v for k, v in ordered.items() if k not in live}
    dead_docs = {k: v for k, v in documented.items() if k not in live}
    mentioned = set(ordered) | set(documented)
    unknown = sorted(i for i in live if i not in mentioned)
    free_live = sorted(i for i in live if i.endswith("-free"))

    print(f"catalogue: {len(live)} ids at {CATALOGUE_URL}", file=out)

    if dead_orders:
        print("\nERROR: an ORDER names an id the catalogue does not serve. "
              "Launching it will fail:", file=out)
        for mid, where in sorted(dead_orders.items()):
            print(f"  {mid}  <- {', '.join(where)}", file=out)

    if dead_docs:
        print("\nWARN: the roster prose names an id the catalogue does not "
              "serve. Stale, or renamed:", file=out)
        for mid, where in sorted(dead_docs.items()):
            print(f"  {mid}  <- {', '.join(where)}", file=out)

    if free_live:
        print(f"\nFREE ids offered ({len(free_live)}). A free tier is paid for "
              f"in DATA, so each needs its terms checked at {PRICING_URL} "
              f"before any private work touches it:", file=out)
        print("  " + ", ".join(free_live), file=out)

    if unknown:
        print(f"\nNEW: {len(unknown)} id(s) the catalogue serves that this repo "
              f"never mentions. Price them before reaching for one:", file=out)
        print("  " + ", ".join(unknown), file=out)

    if not dead_orders and not dead_docs:
        print("\nevery id this repo names is LISTED in the catalogue", file=out)
    # LISTED IS NOT SERVEABLE, and this tool cannot tell the difference.
    # Measured 2026-09-04: `gpt-5-nano` is in the catalogue and returns
    # "UnknownError: Unexpected server error" on every call, twice, on a real
    # order and on a one-word probe. Only an actual call proves an id works.
    print("\nLISTED IS NOT THE SAME AS SERVEABLE: this reads the catalogue and "
          "never calls a model. Prove a new id with one throwaway run before "
          "putting an order behind it.", file=out)
    return EXIT_DEAD_ID if dead_orders else EXIT_OK


def self_test() -> int:
    """Offline. The catalogue is injected, so no network and no real repo."""
    checks, failures = 0, []

    def want(name, cond):
        nonlocal checks
        checks += 1
        if not cond:
            failures.append(name)

    import io

    live = {"gpt-5-nano", "deepseek-v4-flash", "nemotron-3-ultra-free"}

    buf = io.StringIO()
    code = report(live, {"qwen3.7-plus": ["queue/orders/99-x.md"]}, {}, out=buf)
    text = buf.getvalue()
    want("an order naming a dead id exits nonzero", code == EXIT_DEAD_ID)
    want("and the dead id is named in the output", "qwen3.7-plus" in text)
    want("and the order file is named too", "99-x.md" in text)

    buf = io.StringIO()
    code = report(live, {}, {"ox-alpha-free": ["docs/model-routing.md"]}, out=buf)
    text = buf.getvalue()
    want("a dead id in PROSE only is a warning, not a failure", code == EXIT_OK)
    want("and it is still reported", "ox-alpha-free" in text)

    buf = io.StringIO()
    code = report(live, {"gpt-5-nano": ["queue/orders/63-x.md"]}, {}, out=buf)
    text = buf.getvalue()
    want("a live id passes", code == EXIT_OK)
    # THE WORD IS "LISTED", NOT "served" (fixed 2026-09-07). This assertion
    # searched for a sentence the tool has never printed, so it could only ever
    # fail: a dead assertion, the same class as a permission entry matching no
    # command. The wording is not cosmetic either. The tool deliberately says
    # LISTED because being in the catalogue is not proof of entitlement, which
    # is the caveat it closes every report with, so an assertion demanding
    # "served" was asking it to overclaim.
    want("and says so explicitly rather than printing nothing",
         "every id this repo names is LISTED in the catalogue" in text)
    want("free ids are surfaced with their data caveat",
         "nemotron-3-ultra-free" in text and "DATA" in text)
    want("ids the repo never mentions are surfaced as NEW",
         "NEW:" in text and "deepseek-v4-flash" in text)

    # The fault this tool must never have: a failed read looking like a pass.
    ids, why = fetch_catalogue("https://localhost:9/nope", timeout=1)
    want("an unreachable catalogue returns None, never an empty set",
         ids is None and bool(why))

    want("provider prefixes are stripped",
         strip_provider("opencode-go/gpt-5-nano") == "gpt-5-nano")
    want("a bare id survives stripping", strip_provider("gpt-5-nano") == "gpt-5-nano")
    want("an id with no digit is not treated as a model id",
         not (ID_SHAPE.match("some-prose-phrase")
              and any(c.isdigit() for c in "some-prose-phrase")))
    # Both of these were reported as dead model ids on the first real run.
    want("a backticked .py path is not a model id",
         bool(NOT_AN_ID.search("n8n-deploy.py")))
    want("a backticked .md path is not a model id",
         bool(NOT_AN_ID.search("operating-model-v3.md")))
    want("a real id with a dot in its version is still an id",
         not NOT_AN_ID.search("qwen3.7-plus") and bool(ID_SHAPE.match("qwen3.7-plus")))

    for name in failures:
        print(f"FAIL  {name}")
    print(f"{checks - len(failures)}/{checks} checks passed")
    return 1 if failures else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--self-test", action="store_true", dest="self_test")
    ap.add_argument("--url", default=CATALOGUE_URL)
    args = ap.parse_args(argv)

    if args.self_test:
        return self_test()

    live, why = fetch_catalogue(args.url)
    if live is None:
        print(f"COULD NOT READ the catalogue: {why}", file=sys.stderr)
        print("This is NOT a clean result. Nothing was compared.", file=sys.stderr)
        return EXIT_UNREADABLE
    return report(live, order_models(), doc_models())


if __name__ == "__main__":
    sys.exit(main())
