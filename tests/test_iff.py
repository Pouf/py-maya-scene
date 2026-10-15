from __future__ import annotations

import io
from pathlib import Path

import pytest

from py_maya_scene.binary import Chunk, GroupChunk, read_mb, walk, write_mb

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
MB_FILES = sorted(SAMPLES.rglob("*.mb"))


def _dump(root: GroupChunk) -> bytes:
    buf = io.BytesIO()
    write_mb(buf, root)
    return buf.getvalue()


def _header(tag: bytes, size: int, reserved: bytes = b"\x00" * 4) -> bytes:
    return tag + reserved + size.to_bytes(8, "big")


# Root > [HEAD > [VERS "abc"], UVER "x"]. The HEAD group starts 4 bytes past an
# 8-byte boundary (after the root's form type), so this pins both rules: data is
# padded relative to its own start, and groups are not padded.
LAYOUT = (
    _header(b"FOR8", 72)
    + b"Maya"
    + _header(b"FOR8", 28)
    + b"HEAD"
    + _header(b"VERS", 3, b"\xff\x7f\x00\x00")
    + b"abc"
    + b"\x00" * 5
    + _header(b"UVER", 1)
    + b"x"
    + b"\x00" * 7
)


def _layout_tree() -> GroupChunk:
    head = GroupChunk(b"FOR8", b"HEAD", [Chunk(b"VERS", b"abc", b"\xff\x7f\x00\x00")])
    return GroupChunk(b"FOR8", b"Maya", [head, Chunk(b"UVER", b"x")])


@pytest.mark.parametrize(
    "path", MB_FILES, ids=lambda p: p.relative_to(SAMPLES).as_posix()
)
def test_roundtrip_is_byte_identical(path: Path) -> None:
    data = path.read_bytes()
    assert _dump(read_mb(data)) == data


def test_write_layout() -> None:
    assert _dump(_layout_tree()) == LAYOUT


def test_read_layout() -> None:
    root = read_mb(LAYOUT)
    head, uver = root.chunks
    assert isinstance(head, GroupChunk) and isinstance(uver, Chunk)
    assert (root.form_type, head.form_type, uver.data) == (b"Maya", b"HEAD", b"x")
    vers = head.chunks[0]
    assert isinstance(vers, Chunk)
    assert (vers.tag, vers.data, vers.reserved) == (
        b"VERS",
        b"abc",
        b"\xff\x7f\x00\x00",
    )


def test_resized_chunk_updates_ancestors() -> None:
    root = read_mb(LAYOUT)
    head = root.chunks[0]
    assert isinstance(head, GroupChunk)
    vers = head.chunks[0]
    assert isinstance(vers, Chunk)
    vers.data = b"a much longer value"
    reread = read_mb(_dump(root))
    assert [(type(c).__name__, c.tag) for _, c in walk(reread)] == [
        ("GroupChunk", b"FOR8"),
        ("Chunk", b"VERS"),
        ("Chunk", b"UVER"),
    ]
    reread_head = reread.chunks[0]
    assert isinstance(reread_head, GroupChunk)
    reread_vers = reread_head.chunks[0]
    assert isinstance(reread_vers, Chunk)
    assert reread_vers.data == b"a much longer value"


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"FOR4\x00\x00\x00\x04Maya", "not a 64-bit Maya Binary file"),
        (LAYOUT + b"\x00", "unexpected bytes after the root chunk"),
        (LAYOUT[:-1], "overruns its parent"),
        (LAYOUT[:-1] + b"\x01", "non-zero padding"),
    ],
    ids=["for4", "trailing-bytes", "truncated", "non-zero-padding"],
)
def test_rejects_what_cannot_round_trip(data: bytes, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        read_mb(data)
