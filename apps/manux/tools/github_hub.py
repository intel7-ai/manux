"""Dan's GitHub dataset provider adapted to v0.4: env auth, atomic commits, no token UI."""
import base64
import json
import os
from pathlib import Path
import re
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, build_opener, HTTPRedirectHandler

from hub import HubError, MANIFEST_FILE, merge_manifest


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise HubError("허브 주소가 이동했습니다. .env의 저장소 주소를 확인하세요.")


class GitHubDatasetHub:
    def __init__(self, repo_id, token=None, branch="main"):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo_id):
            raise HubError("GitHub 저장소는 소유자/저장소 형식이어야 합니다.")
        if not re.fullmatch(r"[A-Za-z0-9_/-]+", branch) or any(p in ("", ".", "..") for p in branch.split("/")):
            raise HubError("GitHub 브랜치를 확인하세요.")
        self.repo_id, self.branch = repo_id, branch
        self.token = token or os.environ.get("MANUX_GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        self.base = "https://api.github.com/repos/" + repo_id
        self.revision = None

    def request(self, route, method="GET", payload=None, missing=False, raw=False):
        headers = {"User-Agent": "MANUX", "Accept": "application/vnd.github.raw+json" if raw else "application/vnd.github+json",
                   "X-GitHub-Api-Version": "2022-11-28"}
        if self.token: headers["Authorization"] = "Bearer " + self.token
        data = json.dumps(payload).encode() if payload is not None else None
        if data: headers["Content-Type"] = "application/json"
        try:
            with build_opener(NoRedirect()).open(Request(self.base + route, data=data, headers=headers, method=method), timeout=45) as response:
                content = response.read()
                return content if raw else json.loads(content)
        except HTTPError as error:
            if missing and error.code == 404: return None
            raise HubError(f"GitHub 허브 HTTP {error.code} · 저장소 권한·네트워크·동시 업로드를 확인하세요.") from None
        except (OSError, ValueError):
            raise HubError("GitHub 허브 요청 실패 · .env와 네트워크를 확인하세요.") from None

    def check_upload_access(self):
        if not self.token or not self.request("").get("permissions", {}).get("push"):
            raise HubError("GitHub Contents 쓰기 권한을 확인하세요.")

    def read_manifest(self):
        self.request("")
        head = self.request("/git/ref/heads/" + quote(self.branch, safe=""))
        self.revision = head["object"]["sha"]
        raw = self.request("/contents/" + MANIFEST_FILE + "?ref=" + self.revision, missing=True, raw=True)
        return [json.loads(line) for line in (raw or b"").decode().splitlines() if line.strip()]

    def download_file(self, remote_path, dest):
        # Paths are validated by the v0.4 caller; no untrusted download_url is followed.
        path = "/".join(quote(p, safe="") for p in remote_path.split("/"))
        raw = self.request("/contents/" + path + "?ref=" + quote(self.revision or self.branch, safe=""), raw=True)
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(raw)
        return Path(dest)

    def push_files(self, uploads, manifest_lines, message):
        self.check_upload_access()
        latest = self.read_manifest()
        occupied = {row["path"]: row.get("sha256") for row in latest}
        if any(row["path"] in occupied and occupied[row["path"]] != row.get("sha256") for row in manifest_lines):
            raise HubError("원격 경로가 다른 파일에 사용 중입니다. 기존 파일을 보존합니다.")
        parent = self.revision
        tree = self.request("/git/commits/" + parent)["tree"]["sha"]
        entries = []
        manifest = ("\n".join(json.dumps(row, ensure_ascii=False) for row in merge_manifest(latest, manifest_lines)) + "\n").encode()
        files = [(remote, Path(local).read_bytes()) for local, remote in uploads] + [(MANIFEST_FILE, manifest)]
        for path, data in files:
            if len(data) > 90 * 1024 * 1024: raise HubError("90MiB보다 큰 파일은 HF 저장소를 사용하세요.")
            blob = self.request("/git/blobs", "POST", {"content": base64.b64encode(data).decode(), "encoding": "base64"})
            entries.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        tree = self.request("/git/trees", "POST", {"base_tree": tree, "tree": entries})["sha"]
        commit = self.request("/git/commits", "POST", {"message": message, "tree": tree, "parents": [parent]})["sha"]
        self.request("/git/refs/heads/" + quote(self.branch, safe=""), "PATCH", {"sha": commit, "force": False})
        return len(uploads)
