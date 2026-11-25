"""Images the user attaches to an agent prompt or a task (usually screenshots).

They're stored as files under ``<data dir>/attachments`` with random names; an
AgentRun or Task keeps the names in ``images``. Each file belongs to exactly one
owner (a run started on a task gets its own copies of the task's pictures), so
deleting the owner can delete its files. When a run starts they're sent to
Claude as image content blocks next to the prompt text.
"""

from __future__ import annotations

import base64
import binascii
import re
import uuid
from pathlib import Path
from typing import Iterable

from .. import config

# Image types Claude accepts, by media type -> file extension.
MEDIA_TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp"}
_EXT_MEDIA = {ext: mt for mt, ext in MEDIA_TYPES.items()}

# The API takes at most 5 MB of base64 per image, which is 3.75 MB of bytes.
# The UI shrinks bigger screenshots before sending them.
MAX_IMAGE_BYTES = 5 * 1024 * 1024 * 3 // 4
MAX_IMAGES = 10

_NAME = re.compile(r"^[0-9a-f]{32}\.(png|jpg|gif|webp)$")


class AttachmentError(ValueError):
    """A pasted image Mercury can't use; the message is shown to the user."""


def attachments_dir() -> Path:
    path = config.data_dir() / "attachments"
    path.mkdir(parents=True, exist_ok=True)
    return path


def sniff(data: bytes) -> str | None:
    """The image's real media type, from its first bytes."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _decode(data: str) -> bytes:
    if data.startswith("data:"):  # tolerate a full data: URL
        data = data.split(",", 1)[-1]
    try:
        return base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise AttachmentError("An image wasn't valid base64.") from exc


def save_images(images: Iterable[str], limit: int = MAX_IMAGES, where: str = "one message") -> list[str]:
    """Validate and store base64 images; returns their attachment names.

    The media type is taken from the bytes themselves, not from the client.
    Nothing is stored unless every image is valid.
    """
    decoded = [_decode(d) for d in images]
    if len(decoded) > limit:
        raise AttachmentError(f"Attach at most {limit} images to {where}.")
    blobs: list[tuple[bytes, str]] = []
    for raw in decoded:
        media_type = sniff(raw)
        if media_type is None:
            raise AttachmentError("Only PNG, JPEG, GIF and WebP images can be attached.")
        if len(raw) > MAX_IMAGE_BYTES:
            raise AttachmentError(f"An image is {len(raw) / 1_048_576:.1f} MB; the limit is {MAX_IMAGE_BYTES / 1_048_576:.2f} MB.")
        blobs.append((raw, media_type))
    folder = attachments_dir()
    names = []
    for raw, media_type in blobs:
        name = f"{uuid.uuid4().hex}.{MEDIA_TYPES[media_type]}"
        (folder / name).write_bytes(raw)
        names.append(name)
    return names


def path_for(name: str) -> Path | None:
    """The file for an attachment name, or None if the name is malformed or missing."""
    if not _NAME.match(name):
        return None
    path = attachments_dir() / name
    return path if path.is_file() else None


def media_type_for(name: str) -> str:
    return _EXT_MEDIA[name.rsplit(".", 1)[-1]]


def content_blocks(prompt: str, names: Iterable[str]) -> list[dict]:
    """Message content for the Claude API: the images, then the prompt text."""
    blocks: list[dict] = []
    for name in names:
        path = path_for(name)
        if path is None:
            raise AttachmentError(f"A pasted image ({name}) is missing from Mercury's attachments folder.")
        blocks.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type_for(name),
                    "data": base64.b64encode(path.read_bytes()).decode("ascii"),
                },
            }
        )
    if prompt.strip():  # the API rejects empty text blocks
        blocks.append({"type": "text", "text": prompt})
    return blocks


def copy(names: Iterable[str]) -> list[str]:
    """Fresh copies of attachments, for a new owner. Missing files are skipped."""
    folder = attachments_dir()
    copies = []
    for name in names:
        path = path_for(name)
        if path is None:
            continue
        new = f"{uuid.uuid4().hex}.{name.rsplit('.', 1)[-1]}"
        (folder / new).write_bytes(path.read_bytes())
        copies.append(new)
    return copies


def image_blocks_for_tool(names: Iterable[str]) -> list[dict]:
    """MCP tool-result image blocks for attachments that still exist."""
    blocks = []
    for name in names:
        path = path_for(name)
        if path is not None:
            blocks.append(
                {"type": "image", "data": base64.b64encode(path.read_bytes()).decode("ascii"), "mimeType": media_type_for(name)}
            )
    return blocks


def delete(names: Iterable[str]) -> None:
    for name in names:
        path = path_for(name)
        if path is not None:
            path.unlink(missing_ok=True)
