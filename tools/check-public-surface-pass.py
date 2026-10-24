"""Gate: verify a declared public repo inventory matches live GitHub state.

Checks live GitHub state via `gh repo list` for every repo in the expected
set: each must be present, have a non-empty description, have at least one
topic, and match the declared archive state. The handle and the expected
repo lists come from `--handle` and `--expected` (a JSON file carrying
`archived` and `active` arrays). Runs its own parsing/logic self-test first,
against fixtures built in a fresh temp directory, never against live GitHub
as the proof.

Exits 0 printing `PASS: N repos checked (A archived, X active)`, or non-zero
printing `FAIL:` lines.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile

# REAL gh repo list output, captured verbatim 2026-08-30 on this machine and
# neutralised to placeholders for publication: the account facts (repos,
# descriptions) are gone, the shapes they exercised are kept. The sample
# exercises real shapes the invented good/bad fixtures never did:
# repositoryTopics is null for topic-less repos, topic lists are real, and
# isArchived is a real bool.
REAL_GH_FIXTURE = (
    '[{"description":"A private config and memory store (private).",'
    '"isArchived":false,"name":"example-private-store","repositoryTopics":null},'
    '{"description":"An example inventory database. ",'
    '"isArchived":false,"name":"example-inventory-db","repositoryTopics":null},'
    '{"description":"Python wrapper for a public data API: readable names '
    'instead of raw codes, with batching and rate-limit handling. Installs as '
    'example-fred-loader.",'
    '"isArchived":false,"name":"example-fred-loader",'
    '"repositoryTopics":[{"name":"api-wrapper"},{"name":"economics"},'
    '{"name":"fred"},{"name":"python"},{"name":"time-series"}]},'
    '{"description":"Python wrapper for another public data API: readable '
    'names instead of raw endpoints and codes. Installs as example-census-'
    'loader.",'
    '"isArchived":false,"name":"example-census-loader",'
    '"repositoryTopics":[{"name":"api-wrapper"},{"name":"census"},'
    '{"name":"open-data"},{"name":"python"}]},'
    '{"description":"Python toolkit for empirical research: regressions, '
    'forecasting, and result output for papers. Installs as example-research-'
    'toolkit.",'
    '"isArchived":false,"name":"example-research-toolkit",'
    '"repositoryTopics":[{"name":"econometrics"},{"name":"python"},'
    '{"name":"research"},{"name":"statistics"}]}]'
)

# The self-test's placeholder inventory: the same shape and counts as a real
# inventory (5 archived, 15 active), no account facts in it.
FIXTURE_ARCHIVED = {
    "example-earnings",
    "example-transit",
    "example-requests",
    "example-econometrics",
    "example-prediction",
}

FIXTURE_ACTIVE = {
    "example-data-kit",
    "example-research-kit",
    "example-census-loader",
    "example-fred-loader",
    "example-research-toolkit",
    "example-profile",
    "example-site",
    "example-legacy",
    "example-debt-analysis",
    "example-claude-workbench",
    "example-opencode-workbench",
    "example-ai-kit",
    "example-cloud-kit",
    "example-dev-kit",
    "example-machine-sync",
}


def parse_repo_list(path):
    """Load the gh repo list JSON from a file into a list of dicts."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_expected(path):
    """Load {archived: [...], active: [...]} from a JSON file as two sets."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return set(data["archived"]), set(data["active"])


def check_repos(repos, archived, active):
    """Return a list of problem strings, empty when every repo is right.

    Each element of `repos` is one repo dict from the gh repo list output.
    Repos not in the expected sets are ignored.
    """
    problems = []
    all_repos = archived | active
    by_name = {r.get("name"): r for r in repos}
    for name in sorted(all_repos):
        repo = by_name.get(name)
        if repo is None:
            problems.append("%s: missing from gh repo list output" % name)
            continue
        if not repo.get("description"):
            problems.append("%s: description is empty" % name)
        topics = repo.get("repositoryTopics") or []
        if len(topics) == 0:
            problems.append("%s: no topics" % name)
        expected_archived = name in archived
        if bool(repo.get("isArchived")) != expected_archived:
            state = "archived" if repo.get("isArchived") else "active"
            want = "archived" if expected_archived else "active"
            problems.append("%s: is %s, expected %s" % (name, state, want))
    return problems


def self_test():
    """Check parsing + logic against fixtures built in fresh temp dirs."""
    archived, active = FIXTURE_ARCHIVED, FIXTURE_ACTIVE
    all_repos = archived | active
    good = []
    for name in sorted(all_repos):
        good.append({
            "name": name,
            "description": "An analysis or a tool" if name in archived else "A working surface",
            "repositoryTopics": [{"name": "python"}],
            "isArchived": name in archived,
        })
    good.append({
        "name": "not-in-scope",
        "description": "should be ignored",
        "repositoryTopics": [],
        "isArchived": False,
    })
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "good.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(good, f)
        problems = check_repos(parse_repo_list(path), archived, active)
    assert problems == [], problems

    # The parser must survive REAL gh output, not just the invented fixtures:
    # repositoryTopics null and real topic lists are shapes the good/bad cases
    # never carry. The three in-scope repos in the sample must parse clean.
    real = json.loads(REAL_GH_FIXTURE)
    names = {r["name"] for r in real}
    assert {"example-fred-loader", "example-census-loader",
            "example-research-toolkit"} <= names, names
    for r in real:
        assert isinstance(r.get("isArchived"), bool), r
        assert isinstance(r.get("description"), str), r
        topics = r.get("repositoryTopics")
        assert topics is None or isinstance(topics, list), r
    real_bad = {p.split(":", 1)[0] for p in check_repos(real, archived, active)}
    assert not ({"example-fred-loader", "example-census-loader",
                 "example-research-toolkit"} & real_bad), real_bad

    bad = []
    for name in sorted(all_repos):
        bad.append({
            "name": name,
            "description": "x",
            "repositoryTopics": [{"name": "python"}],
            "isArchived": name in archived,
        })
    bad[0]["description"] = ""
    bad[1]["repositoryTopics"] = []
    bad[2]["isArchived"] = not bad[2]["isArchived"]
    renamed = bad[3]["name"]
    bad[3]["name"] = "renamed-out-of-scope"
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "bad.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(bad, f)
        problems = check_repos(parse_repo_list(path), archived, active)
    problem_names = {p.split(":", 1)[0] for p in problems}
    assert bad[0]["name"] in problem_names, problems
    assert bad[1]["name"] in problem_names, problems
    assert bad[2]["name"] in problem_names, problems
    assert renamed in problem_names, problems


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
    ap.add_argument("--expected", required=True,
                    help="path to a JSON file with archived and active arrays")
    args = ap.parse_args(argv)

    try:
        self_test()
    except AssertionError as exc:
        print("FAIL: parsing self-test failed: %s" % exc)
        return 1

    archived, active = load_expected(args.expected)
    gh_repo_list = [
        "gh", "repo", "list", args.handle,
        "--visibility", "public",
        "--limit", "100",
        "--json", "name,description,repositoryTopics,isArchived",
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

    problems = check_repos(repos, archived, active)
    if problems:
        for p in problems:
            print("FAIL: %s" % p)
        return 1

    print("PASS: %d repos checked (%d archived, %d active)"
          % (len(archived | active), len(archived), len(active)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))