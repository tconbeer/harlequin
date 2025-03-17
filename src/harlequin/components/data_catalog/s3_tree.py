from __future__ import annotations

from typing import ClassVar
from urllib.parse import urlsplit

from textual import on, work
from textual.message import Message
from textual.widgets import (
    DirectoryTree,
)
from textual.widgets._tree import TreeNode
from textual.worker import Worker, WorkerState

from harlequin.catalog_cache import recursive_dict
from harlequin.components.data_catalog.tree import HarlequinTree as HarlequinTree
from harlequin.messages import WidgetMounted

try:
    import boto3
except ImportError:
    boto3 = None  # type: ignore


class S3Tree(HarlequinTree[str], inherit_bindings=False):
    COMPONENT_CLASSES: ClassVar[set[str]] = {
        "directory-tree--extension",
        "directory-tree--file",
        "directory-tree--folder",
        "directory-tree--hidden",
    }

    class DataReady(Message):
        def __init__(self, data: dict) -> None:
            self.data = data
            super().__init__()

    def __init__(
        self,
        uri: str,
        data: str | None = None,
        name: str | None = None,
        id: str | None = None,  # noqa: A002
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        self.endpoint_url, self.bucket, self.prefix = self._parse_s3_uri(uri)
        self.catalog_data: dict | None = None
        super().__init__(
            "Root", data, name=name, id=id, classes=classes, disabled=disabled
        )

    ICON_NODE_EXPANDED = DirectoryTree.ICON_NODE_EXPANDED
    ICON_NODE = DirectoryTree.ICON_NODE
    ICON_FILE = DirectoryTree.ICON_FILE

    render_label = DirectoryTree.render_label  # type: ignore[assignment]

    def on_mount(self) -> None:
        self.guide_depth = 3
        self.show_root = False
        self.root.data = self.endpoint_url or "s3:/"
        self.reload()
        self.post_message(WidgetMounted(widget=self))

    @property
    def cache_key(self) -> tuple[str | None, str | None, str | None]:
        return (self.endpoint_url, self.bucket, self.prefix)

    @on(DataReady)
    def build_tree_from_message_data(self, message: S3Tree.DataReady) -> None:
        self.build_tree(message.data)

    def build_tree(self, data: dict) -> None:
        self.catalog_data = data
        self.clear()
        self._build_subtree(data=data, parent=self.root)
        self.root.expand()
        self.loading = False

    @on(Worker.StateChanged)
    async def handle_worker_state_change(self, message: Worker.StateChanged) -> None:
        if message.state == WorkerState.ERROR:
            await self._handle_worker_error(message)

    async def _handle_worker_error(self, message: Worker.StateChanged) -> None:
        if (
            message.worker.name == "_reload_objects"
            and message.worker.error is not None
        ):
            self.post_message(
                self.CatalogError(catalog_type="s3", error=message.worker.error)
            )
            self.loading = False

    @staticmethod
    def _parse_s3_uri(uri: str) -> tuple[str | None, str | None, str | None]:
        """
        Any of these are acceptable:
        my-bucket
        my-bucket/my-prefix
        s3://my-bucket
        s3://my-bucket/my-prefix
        https://my-storage.com/my-bucket/
        https://my-storage.com/my-bucket/my-prefix
        https://my-bucket.s3.amazonaws.com/my-prefix
        https://my-bucket.storage.googleapis.com/my-prefix
        """

        def _is_prefixed_aws_url(netloc: str) -> bool:
            parts = netloc.split(".")
            print(parts)
            if ".".join(parts[1:]) == "s3.amazonaws.com":
                return True
            return False

        def _is_prefixed_gcs_url(netloc: str) -> bool:
            parts = netloc.split(".")
            if ".".join(parts[1:]) == "storage.googleapis.com":
                return True
            return False

        if uri == "all":
            # special keyword so we list all buckets
            return None, None, None

        scheme, netloc, path, *_ = urlsplit(uri)
        path = path.lstrip("/")
        bucket: str | None

        if not scheme:
            assert not netloc
            endpoint_url = None
            bucket = path.split("/")[0]
            prefix = "/".join(path.split("/")[1:])
        elif scheme == "s3":
            endpoint_url = None
            bucket = netloc
            prefix = path
        elif _is_prefixed_aws_url(netloc):
            endpoint_url = None
            bucket = netloc.split(".")[0]
            prefix = path
        elif _is_prefixed_gcs_url(netloc):
            endpoint_url = "https://storage.googleapis.com"
            bucket = netloc.split(".")[0]
            prefix = path
        else:
            endpoint_url = f"{scheme}://{netloc}"
            bucket = path.split("/")[0] or None
            prefix = "/".join(path.split("/")[1:])

        return endpoint_url, bucket, prefix

    def reload(self) -> None:
        self.loading = True
        self._reload_objects()

    @work(thread=True, exclusive=True, exit_on_error=False)
    def _reload_objects(self) -> None:
        if boto3 is None:
            return

        data = {}
        s3 = boto3.resource("s3", endpoint_url=self.endpoint_url)
        if self.bucket is None:
            buckets = [b for b in s3.buckets.all()]
        else:
            buckets = [s3.Bucket(self.bucket)]
        for bucket in buckets:
            data[bucket.name] = recursive_dict()
            object_gen = (
                bucket.objects.filter(Prefix=self.prefix)
                if self.prefix
                else bucket.objects.all()
            )
            for obj in object_gen:
                key_parts = obj.key.split("/")
                target = data[bucket.name]
                for part in key_parts:
                    target = target[part]

        self.post_message(S3Tree.DataReady(data=data))

    def _build_subtree(
        self,
        data: dict[str, dict],
        parent: TreeNode[str],
    ) -> None:
        for k in data:
            if data[k]:
                new_node = parent.add(
                    label=k,
                    data=f"{parent.data}/{k}",
                )
                self._build_subtree(data[k], new_node)
            else:
                parent.add_leaf(label=k, data=f"{parent.data}/{k}")
