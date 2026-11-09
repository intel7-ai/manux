"""모노레포·subtree·소스 압축본에서 사용자 데이터 출력의 저장소 경계를 찾는다."""
from pathlib import Path


def repository_root(start: Path) -> Path | None:
    """가장 가까운 .git(폴더 또는 worktree 파일), 없으면 MANUX 소스 루트.

    고정 parents 인덱스는 분리 저장소에서 상위 임시 폴더 전체를 저장소로 오인한다.
    설치본에는 .git/pyproject가 없으므로 None이다.
    """
    resolved = Path(start).resolve()
    candidates = (resolved, *resolved.parents) if resolved.is_dir() else tuple(resolved.parents)
    for parent in candidates:
        if (parent / ".git").exists():
            return parent
    for parent in candidates:
        if (parent / "pyproject.toml").is_file() and (parent / "src/manux").is_dir():
            return parent
    return None
