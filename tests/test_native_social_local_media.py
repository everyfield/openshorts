from __future__ import annotations

import base64
from pathlib import Path

import pytest

from native_social.local_media import (
    LocalMediaError,
    create_signed_local_media_url,
    list_local_media_files,
    resolve_local_media_path,
    resolve_signed_media_token,
)


@pytest.fixture
def local_media_env(tmp_path, monkeypatch):
    root = tmp_path / "media"
    root.mkdir()
    (root / "job-1").mkdir()
    (root / "job-1" / "clip.mp4").write_bytes(b"video")
    monkeypatch.setenv("NATIVE_SOCIAL_MEDIA_DIR", str(root))
    monkeypatch.setenv(
        "NATIVE_SOCIAL_MEDIA_SIGNING_KEY",
        base64.urlsafe_b64encode(b"k" * 32).decode().rstrip("="),
    )
    monkeypatch.setenv("NATIVE_SOCIAL_PUBLIC_BASE_URL", "https://media.example.test")
    return root


def test_resolve_local_media_path_is_confined_to_media_root(local_media_env):
    path = resolve_local_media_path("job-1/clip.mp4")
    assert path == local_media_env / "job-1" / "clip.mp4"


@pytest.mark.parametrize("bad_path", [
    "../secret.txt",
    "/etc/passwd",
    "job-1/../../secret.txt",
    "job-1\\..\\secret.txt",
    "",
])
def test_rejects_traversal_and_absolute_paths(local_media_env, bad_path):
    with pytest.raises(LocalMediaError):
        resolve_local_media_path(bad_path)


def test_signed_url_resolves_to_local_file(local_media_env):
    url = create_signed_local_media_url("job-1/clip.mp4")
    token = url.rsplit("/", 1)[-1]
    assert url.startswith("https://media.example.test/api/native-social/instagram/media/")
    assert resolve_signed_media_token(token).read_bytes() == b"video"


def test_signed_url_rejects_tampering(local_media_env):
    url = create_signed_local_media_url("job-1/clip.mp4")
    token = url.rsplit("/", 1)[-1]
    with pytest.raises(LocalMediaError):
        resolve_signed_media_token(token + "0")


def test_public_base_must_be_https(local_media_env, monkeypatch):
    monkeypatch.setenv("NATIVE_SOCIAL_PUBLIC_BASE_URL", "http://media.example.test")
    with pytest.raises(LocalMediaError):
        create_signed_local_media_url("job-1/clip.mp4")


def test_missing_media_is_rejected(local_media_env):
    with pytest.raises(LocalMediaError):
        resolve_local_media_path("job-1/missing.mp4")


def test_local_media_listing_returns_only_confined_mp4_files(local_media_env):
    (local_media_env / "job-1" / "metadata.json").write_text("{}", encoding="utf-8")
    (local_media_env / "job-1" / "temp.mov").write_bytes(b"not mp4")
    rows = list_local_media_files()
    assert [relative for relative, _path in rows] == ["job-1/clip.mp4"]
