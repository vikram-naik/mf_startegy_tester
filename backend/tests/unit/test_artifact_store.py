from pathlib import Path

import pytest

from mf_strategy_tester.ingestion.artifacts import ArtifactStore


def test_artifact_store_is_content_addressed_and_idempotent(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)

    first = store.store(b"official source bytes")
    second = store.store(b"official source bytes")

    assert first == second
    assert (tmp_path / first.relative_path).read_bytes() == b"official source bytes"


def test_artifact_store_detects_existing_content_corruption(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    stored = store.store(b"official source bytes")
    artifact_path = tmp_path / stored.relative_path
    artifact_path.write_bytes(b"corrupt")

    with pytest.raises(OSError, match="artifact size mismatch"):
        store.store(b"official source bytes")
