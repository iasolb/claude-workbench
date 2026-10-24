"""Validate a public repository descriptions proposal.

Checks a caller-selected proposal file against live GitHub state via
`gh repo list`. Runs its own parsing self-test first, against a fixture it
builds in a fresh temp directory, never against the real proposal.

Takes `--handle <github-login>` and `--proposal <path>`. Exits 0 printing
`PASS: N repos checked`, or non-zero printing `FAIL:` lines.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

# REAL gh repo list output for THIS command, captured verbatim 2026-08-30 on
# this machine and neutralised to placeholders for publication (the sibling
# gate check-public-surface-pass.py carries the same sample WITH isArchived;
# this caller does not request that field, so its fixture must not carry it
# either). The dict comprehension in main() must survive real field shapes:
# description is a real string, repositoryTopics is null for topic-less repos.
REAL_GH_FIXTURE = (
    '[{"description":"A private config and memory store (private).",'
    '"name":"example-private-store","repositoryTopics":null},'
    '{"description":"An example inventory database. ",'
    '"name":"example-inventory-db","repositoryTopics":null},'
    '{"description":"Python wrapper for a public data API: readable names '
    'instead of raw codes, with batching and rate-limit handling. Installs as '
    'example-fred-loader.",'
    '"name":"example-fred-loader",'
    '"repositoryTopics":[{"name":"api-wrapper"},{"name":"economics"},'
    '{"name":"fred"},{"name":"python"},{"name":"time-series"}]},'
    '{"description":"Python wrapper for another public data API: readable '
    'names instead of raw endpoints and codes. Installs as example-census-'
    'loader.",'
    '"name":"example-census-loader",'
    '"repositoryTopics":[{"name":"api-wrapper"},{"name":"census"},'
    '{"name":"open-data"},{"name":"python"}]},'
    '{"description":"Python toolkit for empirical research: regressions, '
    'forecasting, and result output for papers. Installs as example-research-'
    'toolkit.",'
    '"name":"example-research-toolkit",'
    '"repositoryTopics":[{"name":"econometrics"},{"name":"python"},'
    '{"name":"research"},{"name":"statistics"}]}]'
)

EM_DASH = "\u2014"
TOPIC_RE = re.compile(r"^[a-z0-9-]+$")
PAIR_RE = re.compile(r"^[A-Z][a-z]+ [A-Z][a-z]+$")


def parse_proposal(text):
    """Return {repo: {current, proposed, topics, credit}} from proposal text."""
    sections = {}
    repo = None
    for line in text.splitlines():
        if line.startswith("## "):
            repo = line[3:].strip()
            sections[repo] = {"current": "", "proposed": "", "topics": [], "credit": None}
        elif repo is not None and line.startswith("- "):
            body = line[2:].strip()
            for key in ("current", "proposed", "topics", "credit"):
                if body.startswith(key + ":"):
                    value = body[len(key) + 1:].strip()
                    if key == "topics":
                        sections[repo][key] = [t.strip() for t in value.split(",") if t.strip()]
                    else:
                        sections[repo][key] = value
                    break
    return sections


def self_test():
    """Parse a fixture built in a fresh temp dir; assert the parser is sane."""
    fixture = (
        "## ExampleRepo\n"
        "\n"
        "- current: Example Author, Sample Contributor\n"
        "- proposed: Does a thing for people\n"
        "- topics: python, data-tools, api\n"
        "- credit: Authors: Example Author and Sample Contributor\n"
        "- [ ] approve\n"
        "\n"
        "## EmptyRepo\n"
        "\n"
        "- current: EMPTY\n"
        "- proposed: A tool that does the other thing\n"
        "- topics: shell, ssh, sync, tools\n"
        "- [ ] approve\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "fixture.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(fixture)
        with open(path, "r", encoding="utf-8-sig") as f:
            sections = parse_proposal(f.read())
    assert set(sections) == {"ExampleRepo", "EmptyRepo"}, set(sections)
    ex = sections["ExampleRepo"]
    assert ex["current"] == "Example Author, Sample Contributor", ex["current"]
    assert ex["proposed"] == "Does a thing for people", ex["proposed"]
    assert ex["topics"] == ["python", "data-tools", "api"], ex["topics"]
    assert ex["credit"] == "Authors: Example Author and Sample Contributor", ex["credit"]
    em = sections["EmptyRepo"]
    assert em["current"] == "EMPTY", em["current"]
    assert em["proposed"] == "A tool that does the other thing", em["proposed"]
    assert em["topics"] == ["shell", "ssh", "sync", "tools"], em["topics"]
    assert em["credit"] is None, em["credit"]

    # The gh parsing must survive REAL output, not just the markdown fixture.
    # The exact comprehension main() uses is exercised against the captured
    # sample: every repo resolves to a name and a description, and the three
    # in-scope repos in the sample have non-empty descriptions.
    real = json.loads(REAL_GH_FIXTURE)
    gh_repos = {r["name"]: r["description"] for r in real}
    assert set(gh_repos) >= {"example-fred-loader", "example-census-loader",
                             "example-research-toolkit"}, set(gh_repos)
    for name in ("example-fred-loader", "example-census-loader",
                 "example-research-toolkit"):
        assert gh_repos[name], gh_repos[name]


def main(argv):
    if "--self-test" in argv:
        try:
            self_test()
        except AssertionError as exc:
            print("FAIL: parsing self-test failed: %s" % exc)
            return 1
        print("SELFTEST PASS")
        return 0

    ap = argparse.ArgumentParser()
    ap.add_argument("--handle", required=True,
                    help="GitHub login to list public repos for")
    ap.add_argument("--proposal", required=True,
                    help="path to the proposal markdown file")
    args = ap.parse_args(argv)

    try:
        self_test()
    except AssertionError as exc:
        print("FAIL: parsing self-test failed: %s" % exc)
        return 1

    gh_repo_list = [
        "gh", "repo", "list", args.handle,
        "--visibility", "public",
        "--limit", "100",
        "--json", "name,description,repositoryTopics",
    ]

    proc = subprocess.run(gh_repo_list, capture_output=True, text=True)
    if proc.returncode != 0:
        print("FAIL: gh repo list failed: %s" % proc.stderr.strip())
        return 1
    try:
        repos = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        print("FAIL: could not parse gh output: %s" % exc)
        return 1

    gh_repos = {r["name"]: r["description"] for r in repos}

    with open(args.proposal, "r", encoding="utf-8-sig") as f:
        sections = parse_proposal(f.read())

    problems = []

    for name in sorted(gh_repos):
        if name not in sections:
            problems.append("missing section for repo %s" % name)
    for name in sorted(sections):
        if name not in gh_repos:
            problems.append("section names unknown repo %s" % name)

    for name, sec in sections.items():
        proposed = sec["proposed"]
        current = sec["current"]
        if not proposed:
            problems.append("%s: proposed is empty" % name)
        if len(proposed) > 100:
            problems.append("%s: proposed is %d characters (max 100)" % (name, len(proposed)))
        if EM_DASH in proposed:
            problems.append("%s: proposed contains an em dash" % name)
        if proposed == "KEEP" and current == "EMPTY":
            problems.append("%s: proposed KEEP but current is EMPTY" % name)
        topics = sec["topics"]
        if not (3 <= len(topics) <= 6):
            problems.append("%s: has %d topics (need 3-6)" % (name, len(topics)))
        for t in topics:
            if not TOPIC_RE.match(t):
                problems.append("%s: topic %r is not lowercase letters/digits/hyphens" % (name, t))
        if current != "EMPTY":
            parts = [p.strip() for p in current.split(",")]
            pairs = [p for p in parts if PAIR_RE.match(p)]
            if len(pairs) >= 2 and sec["credit"] is None:
                problems.append("%s: names-as-description without a credit line" % name)

    if problems:
        for p in problems:
            print("FAIL: %s" % p)
        return 1

    print("PASS: %d repos checked" % len(gh_repos))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
