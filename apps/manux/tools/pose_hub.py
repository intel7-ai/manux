"""Validated per-action snapshot datasets, separate from temporal TCN recordings."""
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import tempfile

import hub
from manux.data.pose_studio import ACTION_IDS


def validate(doc, classes=()):
    if not isinstance(doc, dict) or doc.get("schema") != "manux-pose-snapshots/2":
        raise ValueError("포즈 데이터셋 형식이 맞지 않습니다.")
    person = doc.get("person", "")
    if not re.fullmatch(r"[a-z0-9_-]{1,32}", str(person)) or doc.get("split") not in ("train", "eval"):
        raise ValueError("포즈 데이터셋에 별명과 train/eval 구분이 필요합니다. 별명을 저장한 뒤 다시 수집하세요.")
    samples = doc.get("samples")
    if not isinstance(samples, list) or len(samples) > 100000: raise ValueError("포즈 표본 목록을 확인하세요.")
    cleaned = []
    for sample in samples:
        label = sample.get("groundTruthAction")
        if label not in ACTION_IDS: raise ValueError("포즈 라벨을 확인하세요.")
        points = []
        for field in ("rawLandmarks", "rawWorld"):
            landmarks = sample.get(field)
            if landmarks is None and field == "rawWorld": points.append(None); continue
            if not isinstance(landmarks, list) or len(landmarks) != 21: raise ValueError("포즈 관절 개수를 확인하세요.")
            values = []
            for point in landmarks:
                if not isinstance(point, dict) or set(point) != {"x", "y", "z"}: raise ValueError("관절 좌표 형식을 확인하세요.")
                if not all(isinstance(point[k], (int,float)) and math.isfinite(point[k]) and abs(point[k]) < 1e6 for k in point):
                    raise ValueError("유효하지 않은 관절 좌표입니다.")
                values.append({k: float(point[k]) for k in ("x", "y", "z")})
            points.append(values)
        if classes and label not in classes: continue
        # Project onto the public schema; arbitrary local metadata never leaves this machine.
        identifier = str(sample.get("sampleId", ""))
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", identifier): raise ValueError("표본 식별자가 잘못되었습니다.")
        cleaned.append({"sampleId":identifier,"groundTruthAction":label,"rawLandmarks":points[0],"rawWorld":points[1],
                        "isFailureSample":bool(sample.get("isFailureSample")),"trackingValid":True})
    return {"schema":"manux-pose-snapshots/2", "person":person, "split":doc["split"],
            "session_id":str(doc.get("session_id", "")) if re.fullmatch(r"[0-9a-f]{32}", str(doc.get("session_id", ""))) else "",
            "samples":cleaned}


def push(client, root, profile, classes, cancelled=lambda:False):
    manifest = client.read_manifest(); existing = {r.get("sha256") for r in manifest}; entries=[]; uploads=[]
    with tempfile.TemporaryDirectory(prefix="manux-pose-export-") as temporary:
        for path in sorted(Path(root).rglob("*.json")):
            if cancelled(): raise ValueError("포즈 공유를 취소했습니다.")
            if path.stat().st_size > 64*1024*1024: raise ValueError("포즈 데이터 파일이 너무 큽니다.")
            doc = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(doc, dict) or doc.get("person") != profile: continue
            cleaned=validate(doc,classes)
            if not cleaned["samples"]: continue
            raw=json.dumps(cleaned,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode()
            digest=sha256(raw).hexdigest()
            if digest in existing: continue
            existing.add(digest)
            staged=Path(temporary)/(digest+".json");staged.write_bytes(raw)
            remote=f"pose-datasets/{profile}/{cleaned['split']}/{digest}.json"
            entries.append({"type":"pose_dataset","path":remote,"sha256":digest,"person":profile,"split":cleaned["split"],
                            "size_bytes":len(raw),
                            "classes":sorted({s["groundTruthAction"] for s in cleaned["samples"]}),"samples":len(cleaned["samples"])})
            uploads.append((staged,remote))
        if cancelled(): raise ValueError("포즈 공유를 취소했습니다.")
        if uploads: client.push_files(uploads,hub.merge_manifest(manifest,entries),"Share action-specific pose samples")
    return len(uploads)


def pull(client, root, classes, cancelled=lambda:False):
    root=Path(root)/"received";root.mkdir(parents=True,exist_ok=True);count=0
    for row in client.read_manifest():
        if row.get("type") != "pose_dataset" or classes and not set(classes).intersection(row.get("classes",[])): continue
        if cancelled(): raise ValueError("포즈 다운로드를 취소했습니다.")
        digest=str(row.get("sha256", ""));remote=str(row.get("path", ""))
        if not re.fullmatch(r"[0-9a-f]{64}",digest) or not re.fullmatch(r"pose-datasets/[a-z0-9_-]{1,32}/(?:train|eval)/[0-9a-f]{64}\.json",remote):
            raise ValueError("원격 포즈 경로가 잘못되었습니다.")
        dest=root/(digest+".json")
        if dest.exists():
            if hub.file_sha256(dest)!=digest: raise ValueError("기존 포즈 파일이 변경되었습니다. 기존 파일을 보존합니다.")
            continue
        with tempfile.TemporaryDirectory(prefix=".pose-",dir=root) as temporary:
            staged=Path(temporary)/"data.json";client.download_file(remote,staged)
            if staged.stat().st_size > 64*1024*1024: raise ValueError("포즈 데이터 파일이 너무 큽니다.")
            if hub.file_sha256(staged)!=digest: raise ValueError("포즈 다운로드 해시가 다릅니다.")
            raw_doc=json.loads(staged.read_text(encoding="utf-8"))
            doc=validate(raw_doc)
            if raw_doc != doc: raise ValueError("공개 포즈 형식 외의 필드가 있습니다.")
            if doc["person"]!=row.get("person") or doc["split"]!=row.get("split"): raise ValueError("포즈 인덱스와 본문이 다릅니다.")
            os.link(staged,dest);count+=1
    return count
