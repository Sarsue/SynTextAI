"""Bringing documents in from where a company already keeps them.

WHAT THIS IS, AND WHAT IT DELIBERATELY IS NOT

A customer picks documents in Google Drive or SharePoint using that provider's
own picker, in their own browser, and those documents arrive here as ordinary
files: same storage, same workspace, same ingestion path, same citations. A
connector is a *source*, not a second pipeline. Anything that made imported
documents special would have to be maintained twice and would drift.

**Nothing is stored to keep access.** The browser hands us a short-lived access
token for one import, we use it immediately to fetch the bytes, and it is gone
when the request ends. There is no refresh token in the database, no long-lived
grant against a customer's Drive, and therefore nothing to leak if this database
were ever read by somebody who should not have it. That is a deliberate v1
choice with a real cost: no automatic sync. Continuous sync means storing a
refresh token, which means encrypting it, rotating it, and being the kind of
target that holds standing access to a law firm's document store. Worth doing
when a customer asks for it, and not before.

**The scope matters as much as the code.** With Google, `drive.readonly` is a
*restricted* scope: production use requires a third-party security assessment
that costs weeks and money. The picker flow used here needs only `drive.file`,
which grants access to the files the customer explicitly chose and nothing
else. That is both cheaper to ship and easier to defend in a sales
conversation, since we never hold the keys to their whole Drive.

WHY THE PROVIDERS SHARE ONE SHAPE

Both come down to the same two questions: what is this item called, and give me
its bytes. Everything after that is the pipeline that already exists. Keeping
the adapters this thin is what stops a second provider becoming a second
product.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, Protocol

import httpx

logger = logging.getLogger(__name__)

# Long enough for a large document over a slow link, short enough that a
# provider having a bad day does not hold a request open indefinitely.
FETCH_TIMEOUT = 60.0

# Matches the uploader's own ceiling. A connector must not be a way around the
# limit that a drag-and-drop upload is held to.
MAX_IMPORT_BYTES = 100 * 1024 * 1024

# What the processors can actually read. Offering more would import documents
# that sit in the list and answer nothing, which is the failure this codebase
# has fixed twice already.
SUPPORTED_EXTENSIONS = ("pdf", "docx", "txt", "md")

# Google Workspace documents are not files until they are exported, so a Doc
# comes across as a PDF. Anything else Google-native is refused rather than
# silently imported as something unreadable.
GOOGLE_EXPORTS = {
    "application/vnd.google-apps.document": ("application/pdf", "pdf"),
    "application/vnd.google-apps.presentation": ("application/pdf", "pdf"),
}


class ImportRefused(Exception):
    """The document cannot be imported, with a reason a person can act on."""


@dataclass
class RemoteDocument:
    """One document fetched from a provider, ready for the normal pipeline."""

    filename: str
    content: bytes

    @property
    def size(self) -> int:
        return len(self.content)


class Connector(Protocol):
    """Two questions, per provider."""

    name: str

    async def fetch(self, item_id: str, access_token: str) -> RemoteDocument:
        ...


def _ensure_supported(filename: str) -> None:
    extension = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if extension not in SUPPORTED_EXTENSIONS:
        raise ImportRefused(
            f"{filename} is a .{extension or 'file'}, and only "
            f"{', '.join('.' + e for e in SUPPORTED_EXTENSIONS)} can be read."
        )


def _ensure_size(filename: str, size: int) -> None:
    if size > MAX_IMPORT_BYTES:
        raise ImportRefused(
            f"{filename} is {size / 1024 / 1024:.1f}MB, over the "
            f"{MAX_IMPORT_BYTES // 1024 // 1024}MB limit."
        )


class GoogleDriveConnector:
    """Drive, through the file picker.

    The token is the customer's own, minted in their browser for the files they
    chose, and it is used here and discarded.
    """

    name = "google_drive"

    async def fetch(self, item_id: str, access_token: str) -> RemoteDocument:
        headers = {"Authorization": f"Bearer {access_token}"}
        async with httpx.AsyncClient(timeout=FETCH_TIMEOUT) as client:
            meta = await client.get(
                f"https://www.googleapis.com/drive/v3/files/{item_id}",
                params={"fields": "name,mimeType,size", "supportsAllDrives": "true"},
                headers=headers,
            )
            if meta.status_code == 404:
                raise ImportRefused("That document is no longer in Drive.")
            if meta.status_code in (401, 403):
                raise ImportRefused(
                    "Google refused access to that document. Pick it again to "
                    "grant access."
                )
            meta.raise_for_status()
            info = meta.json()

            filename = info.get("name") or item_id
            mime = info.get("mimeType") or ""

            if mime in GOOGLE_EXPORTS:
                export_mime, extension = GOOGLE_EXPORTS[mime]
                # A Google Doc has no bytes of its own; it becomes a document
                # when exported. PDF keeps the pagination, which is what makes
                # a citation resolve to a page somebody can look at.
                if not filename.lower().endswith(f".{extension}"):
                    filename = f"{filename}.{extension}"
                response = await client.get(
                    f"https://www.googleapis.com/drive/v3/files/{item_id}/export",
                    params={"mimeType": export_mime},
                    headers=headers,
                )
            elif mime.startswith("application/vnd.google-apps"):
                raise ImportRefused(
                    f"{filename} is a Google {mime.rsplit('.', 1)[-1]}, which "
                    "cannot be imported. Export it to PDF first."
                )
            else:
                _ensure_supported(filename)
                declared = int(info.get("size") or 0)
                if declared:
                    _ensure_size(filename, declared)
                response = await client.get(
                    f"https://www.googleapis.com/drive/v3/files/{item_id}",
                    params={"alt": "media", "supportsAllDrives": "true"},
                    headers=headers,
                )

            response.raise_for_status()
            content = response.content

        _ensure_supported(filename)
        _ensure_size(filename, len(content))
        if not content:
            raise ImportRefused(f"{filename} came back empty from Drive.")
        return RemoteDocument(filename=filename, content=content)


class SharePointConnector:
    """SharePoint and OneDrive, through Microsoft Graph.

    Graph addresses a document as a drive plus an item, so the id here carries
    both, separated by a colon, which is what the Microsoft picker returns.
    """

    name = "sharepoint"

    async def fetch(self, item_id: str, access_token: str) -> RemoteDocument:
        if ":" in item_id:
            drive_id, item = item_id.split(":", 1)
            base = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/items/{item}"
        else:
            # OneDrive for the signed-in person, when the picker gives only an
            # item id.
            base = f"https://graph.microsoft.com/v1.0/me/drive/items/{item_id}"

        headers = {"Authorization": f"Bearer {access_token}"}
        async with httpx.AsyncClient(timeout=FETCH_TIMEOUT, follow_redirects=True) as client:
            meta = await client.get(base, headers=headers)
            if meta.status_code == 404:
                raise ImportRefused("That document is no longer in SharePoint.")
            if meta.status_code in (401, 403):
                raise ImportRefused(
                    "Microsoft refused access to that document. Your "
                    "administrator may need to approve this."
                )
            meta.raise_for_status()
            info = meta.json()

            filename = info.get("name") or item_id
            _ensure_supported(filename)
            declared = int(info.get("size") or 0)
            if declared:
                _ensure_size(filename, declared)

            # follow_redirects, because Graph answers a content request with a
            # signed location rather than the bytes.
            response = await client.get(f"{base}/content", headers=headers)
            response.raise_for_status()
            content = response.content

        _ensure_size(filename, len(content))
        if not content:
            raise ImportRefused(f"{filename} came back empty from SharePoint.")
        return RemoteDocument(filename=filename, content=content)


_CONNECTORS = {
    GoogleDriveConnector.name: GoogleDriveConnector(),
    SharePointConnector.name: SharePointConnector(),
}


def get_connector(provider: str) -> Connector:
    connector = _CONNECTORS.get((provider or "").strip().lower())
    if connector is None:
        raise ImportRefused(f"{provider} is not a source this app can import from.")
    return connector
