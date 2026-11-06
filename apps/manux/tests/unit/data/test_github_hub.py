from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))
from github_hub import GitHubDatasetHub


def test_github_upload_is_one_non_force_commit_and_keeps_concurrent_manifest(tmp_path):
    hub = GitHubDatasetHub("synthetic/data", token="synthetic-only")
    calls = []
    def request(route,method="GET",payload=None,**kwargs):
        calls.append((route,method,payload))
        if route == "": return {"permissions":{"push":True}}
        if route.startswith("/git/ref/heads/"): return {"object":{"sha":"parent"}}
        if route.startswith("/contents/"): return b'{"type":"recording","path":"recordings/other","sha256":"other"}\n'
        if route == "/git/commits/parent": return {"tree":{"sha":"tree"}}
        if route == "/git/blobs": return {"sha":"blob"}
        if route == "/git/trees": return {"sha":"new-tree"}
        if route == "/git/commits":
            assert payload["parents"] == ["parent"]
            return {"sha":"new-commit"}
        return {}
    hub.request = request
    path=tmp_path/"sample";path.write_bytes(b"synthetic")
    assert hub.push_files([(path,"recordings/new")],[{"type":"recording","path":"recordings/new","sha256":"new"}],"synthetic") == 1
    update=calls[-1]
    assert update == ("/git/refs/heads/main","PATCH",{"sha":"new-commit","force":False})
    import base64,json
    manifest=json.loads(base64.b64decode([p for r,m,p in calls if r=="/git/blobs"][-1]["content"]).decode().splitlines()[0])
    assert manifest["path"] in ("recordings/new","recordings/other")
    assert len([p for r,m,p in calls if r=="/git/blobs"]) == 2
