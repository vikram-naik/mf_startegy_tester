import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile


@dataclass(frozen=True)
class StoredArtifact:
    sha256: str
    byte_size: int
    relative_path: str


class ArtifactStore:
    """Content-addressed storage that never overwrites an existing payload."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def store(self, content: bytes) -> StoredArtifact:
        sha256 = hashlib.sha256(content).hexdigest()
        relative_path = Path("sha256") / sha256[:2] / sha256
        target = self._root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)

        if target.exists():
            self._verify_existing(target, sha256, len(content))
        else:
            self._create_without_overwrite(target, content)

        return StoredArtifact(
            sha256=sha256,
            byte_size=len(content),
            relative_path=relative_path.as_posix(),
        )

    def read(self, relative_path: str, *, expected_sha256: str) -> bytes:
        """Read and verify an artifact without allowing paths outside the store."""
        target = (self._root / relative_path).resolve()
        if not target.is_relative_to(self._root):
            raise ValueError("artifact path resolves outside the configured raw-data directory")
        content = target.read_bytes()
        actual_sha256 = hashlib.sha256(content).hexdigest()
        if actual_sha256 != expected_sha256:
            raise OSError(f"artifact checksum mismatch at {target}")
        return content

    def _create_without_overwrite(self, target: Path, content: bytes) -> None:
        temporary_path: Path | None = None
        try:
            with NamedTemporaryFile(
                dir=target.parent, prefix=".capture-", delete=False
            ) as temporary:
                temporary.write(content)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_path = Path(temporary.name)
            try:
                os.link(temporary_path, target)
            except FileExistsError:
                self._verify_existing(target, hashlib.sha256(content).hexdigest(), len(content))
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    @staticmethod
    def _verify_existing(target: Path, expected_sha256: str, expected_size: int) -> None:
        if target.stat().st_size != expected_size:
            raise OSError(f"artifact size mismatch at {target}")
        actual_sha256 = hashlib.sha256(target.read_bytes()).hexdigest()
        if actual_sha256 != expected_sha256:
            raise OSError(f"artifact checksum mismatch at {target}")
