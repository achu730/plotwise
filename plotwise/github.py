"""Deposits one figure version in the scientist's own GitHub repository, as a single commit.

The access token is used for this one request only. Plotwise never writes it to disk, to the
session or to the AI log, and never sends it anywhere but the GitHub API.
"""
import base64
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

API = os.environ.get("PLOTWISE_GITHUB_API", "https://api.github.com")
WEB = os.environ.get("PLOTWISE_GITHUB_WEB", "https://github.com")


class GitHubError(Exception):
    pass


def clean_folder(folder):
    parts = [re.sub(r"[^\w.\- ]+", "_", p).strip() for p in re.split(r"[\\/]+", folder or "")]
    parts = [p for p in parts if p and p not in (".", "..")]
    return "/".join(parts)


def _call(method, path, token, body=None):
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "Plotwise"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(API + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read() or b"{}").get("message", "")
        except ValueError:
            msg = ""
        return e.code, {"message": msg}
    except urllib.error.URLError as e:
        raise GitHubError(f"Could not reach GitHub ({e.reason}).") from None


def _fail(status, what, body):
    msg = (body or {}).get("message", "")
    if status == 401:
        raise GitHubError("GitHub did not accept the token. Check that it was copied whole and hasn't expired.")
    if status == 403 or "not accessible" in msg.lower():
        raise GitHubError("The token can't write to this repository. Give it 'Contents: Read and write' access "
                          "to this repository (fine-grained token) or the 'repo' scope (classic token).")
    raise GitHubError(f"GitHub refused to {what} ({status}{': ' + msg if msg else ''}).")


def deposit(token, repo, branch, folder, files, message):
    """files: {path inside folder: bytes}. Returns links to the commit and the folder."""
    token = (token or "").strip()
    repo = (repo or "").strip().removeprefix("https://github.com/").removesuffix(".git").strip("/")
    branch = (branch or "").strip() or None
    folder = clean_folder(folder)
    if not token:
        raise GitHubError("Paste a GitHub access token first.")
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        raise GitHubError("Write the repository as owner/name, for example achu730/lhcb-figures.")
    if branch and not re.fullmatch(r"[\w./-]+", branch):
        raise GitHubError("That branch name has characters GitHub doesn't allow.")
    q = urllib.parse.quote

    st, info = _call("GET", f"/repos/{repo}", token)
    if st == 404:
        raise GitHubError(f"Repository {repo} not found, or the token can't see it. Create it on GitHub first "
                          f"(it can be private) and give the token access to it.")
    if st != 200:
        _fail(st, "open the repository", info)
    if info.get("permissions") and not info["permissions"].get("push"):
        raise GitHubError(f"Your account can read {repo} but not write to it.")
    branch = branch or info.get("default_branch") or "main"

    st, ref = _call("GET", f"/repos/{repo}/git/ref/heads/{q(branch)}", token)
    if st == 409:  # an empty repository has no commits to build on: start it with the README
        first = "README.md" if "README.md" in files else next(iter(files))
        path = f"{folder}/{first}" if folder else first
        st, body = _call("PUT", f"/repos/{repo}/contents/{q(path)}", token,
                         {"message": message, "branch": branch, "content": base64.b64encode(files[first]).decode()})
        if st not in (200, 201):
            _fail(st, "start the empty repository", body)
        st, ref = _call("GET", f"/repos/{repo}/git/ref/heads/{q(branch)}", token)
    elif st == 404:  # new branch, made from the default branch
        st, base = _call("GET", f"/repos/{repo}/git/ref/heads/{q(info.get('default_branch') or 'main')}", token)
        if st != 200:
            _fail(st, "find the default branch", base)
        st, ref = _call("POST", f"/repos/{repo}/git/refs", token, {"ref": f"refs/heads/{branch}", "sha": base["object"]["sha"]})
        if st not in (200, 201):
            _fail(st, f"create branch {branch}", ref)
    if st not in (200, 201) or not ref or "object" not in ref:
        _fail(st, f"read branch {branch}", ref)
    parent = ref["object"]["sha"]
    st, pc = _call("GET", f"/repos/{repo}/git/commits/{parent}", token)
    if st != 200:
        _fail(st, "read the latest commit", pc)

    tree = []
    for name, content in files.items():
        st, blob = _call("POST", f"/repos/{repo}/git/blobs", token,
                         {"content": base64.b64encode(content).decode(), "encoding": "base64"})
        if st not in (200, 201):
            _fail(st, f"upload {name}", blob)
        tree.append({"path": f"{folder}/{name}" if folder else name, "mode": "100644", "type": "blob", "sha": blob["sha"]})
    st, t = _call("POST", f"/repos/{repo}/git/trees", token, {"base_tree": pc["tree"]["sha"], "tree": tree})
    if st not in (200, 201):
        _fail(st, "build the commit", t)
    st, c = _call("POST", f"/repos/{repo}/git/commits", token, {"message": message, "tree": t["sha"], "parents": [parent]})
    if st not in (200, 201):
        _fail(st, "make the commit", c)
    st, r = _call("PATCH", f"/repos/{repo}/git/refs/heads/{q(branch)}", token, {"sha": c["sha"]})
    if st != 200:
        _fail(st, f"move branch {branch} to the new commit", r)
    return {"repo": repo, "branch": branch, "folder": folder, "commit": c["sha"],
            "commit_url": f"{WEB}/{repo}/commit/{c['sha']}",
            "folder_url": f"{WEB}/{repo}/tree/{q(branch)}/{q(folder)}" if folder else f"{WEB}/{repo}/tree/{q(branch)}"}
