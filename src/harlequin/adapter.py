from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Sequence

from textual_fastdatatable.backend import AutoBackendType

from harlequin.autocomplete.completion import HarlequinCompletion
from harlequin.catalog import Catalog
from harlequin.options import HarlequinAdapterOption, HarlequinCopyFormat
from harlequin.transaction_mode import HarlequinTransactionMode


class HarlequinCursor(ABC):
    @abstractmethod
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    @abstractmethod
    def columns(self) -> list[tuple[str, str]]:
        """
        Gets a list of columns for the result set of the cursor.

        Returns: list[tuple[str, str]], where each tuple is the (name, type) of
            a column, where type should be a short (1-3 character) string. The
            columns must be ordered in the same order as the data returned by
            fetchall()
        """
        pass

    @abstractmethod
    def set_limit(self, limit: int) -> "HarlequinCursor":
        """
        Limits the number of results for future calls to fetchall().

        Args:
            limit (int): The maximum number of records to be returned
            by future calls to fetchall().

        Returns: HarlequinCursor, either a reference to self or a new
            cursor with the limit applied.
        """
        pass

    @abstractmethod
    def fetchall(self) -> AutoBackendType | None:
        """
        Returns data from the cursor's result set. Can return any type supported
        by textual-fastdatatable. If set_limit is called prior to fetchall,
        this method only returns the limited number of records. If the query returns
        no rows, fetchall should return None.

        Returns:
            pyarrow.Table |
            pyarrow.Record Batch |
            Sequence[Iterable[Any]] |
            Mapping[str, Sequence[Any]] |
            None
        """
        pass


class HarlequinConnection(ABC):
    """
    A Connection is created by the Adapter, and represents the main interface into
    the database.
    """

    @abstractmethod
    def __init__(self, *args: Any, init_message: str = "", **kwargs: Any) -> None:
        """
        Args:
            init_message (str): If set, Harlequin will notify the user with the
            message after the connection is created.
        """
        self.init_message = init_message

    @abstractmethod
    def execute(self, query: str) -> HarlequinCursor | None:
        """
        Executes query and returns a cursor (for a select stmt) or None. Raises
        HarlequinQueryError if the database raises an error in response to the query.

        Args:
            query (str): The text of a single query to execute

        Returns: HarlequinCursor | None

        Raises: HarlequinQueryError
        """
        pass

    def cancel(self) -> None:
        """
        Cancels/interrupts all in-flight queries previously executed with `execute`.

        After implementing this method, set the adapter class variable
        IMPLEMENTS_CANCEL to True to show the cancel button in the Harlequin UI.
        """
        return None

    @abstractmethod
    def get_catalog(self) -> Catalog:
        """
        Introspects the connected database and returns a Catalog object with
        items for each database, schema, table, view, column, etc.

        Returns: Catalog
        """
        pass

    def get_completions(self) -> list[HarlequinCompletion]:
        """
        Returns a list of extra completions to make available to the Query Editor's
        autocomplete. These could be dialect-specific functions, keywords, etc.
        Harlequin ships with a basic list of common ANSI-SQL keywords and functions.

        It is not necessary to provide completions for Catalog items, since Harlequin
        will create those from the Catalog.

        Returns: list[HarlequinCompletion]
        """
        return []

    def copy(
        self, query: str, path: Path, format_name: str, options: dict[str, Any]
    ) -> None:
        """
        Exports data returned by query to a file or directory at path, using
        options.
        Args:
            query (str): The text of the query (select stmt) to be executed.
            path (Path): The destination location for the file(s) to be written.
            format_name (str): The name of the selected export format.
            options (dict[str, Any]): A dict of format option names and values.

        Returns: None

        Raises:
            NotImplementedError if the adapter does not have copy functionality.
            HarlequinCopyError for all other exceptions during export.
        """
        raise NotImplementedError

    def validate_sql(self, text: str) -> str:
        """
        Parses text as one or more queries; returns text if parsing does not result
        in an error; otherwise returns the empty string ("").

        Args:
            text (str): The text, which may compose one or more queries and partial
                queries.

        Returns: str, either the original text or the empty string ("")

        Raises: NotImplementedError if the adapter does not provide this optional
            functionality.
        """
        raise NotImplementedError

    def close(self) -> None:
        """
        Closes the connection, if necessary. This function is called when the app
        quits.

        Returns: None
        """
        return None

    @property
    def transaction_mode(self) -> HarlequinTransactionMode | None:
        """
        The user-facing label of the currently-active transaction mode.

        Returns None if the adapter does not support different
        transaction modes.

        Returns: HarlequinTransactionMode | None
        """
        return None

    def toggle_transaction_mode(self) -> HarlequinTransactionMode | None:
        """
        Switches to the next transaction mode in the adapter's sequence of modes
        and returns the new mode.

        No-ops and returns None if the adapter does not support different
        transaction modes.

        Returns: HarlequinTransactionMode, the new mode, after toggling, or None.
        """
        return None


class HarlequinAdapter(ABC):
    """
    A HarlequinAdapter is the main abstraction for a database backend for
    Harlequin.

    It must declare its configuration setting the ADAPTER_OPTIONS
    class variable.

    Adapters are initialized with a conn_str (a tuple of strings), and
    kwargs that represent CLI options. Adapters must be robust to receiving
    both subsets and supersets of their defined options as kwargs. They should
    disregard any extra (unexpected) kwargs. They should not rely on the
    option's default values, as those will not be passed by the CLI when
    initializing the adapter.

    Adapters can provide client-side (Harlequin adapter) details using the
    ADAPTER_DETAILS class variable. It is expected to be formatted as markdown.

    Adapters can also provide server-side (DB driver) details using the
    ADAPTER_DRIVER_DETAILS class variable. It is expected to be formatted
    as markdown.
    """

    ADAPTER_OPTIONS: list[HarlequinAdapterOption] | None = None
    COPY_FORMATS: list[HarlequinCopyFormat] | None = None
    """DEPRECATED. Adapter Copy formats are now ignored by Harlequin."""
    IMPLEMENTS_CANCEL = False
    ADAPTER_DETAILS: str | None = None
    ADAPTER_DRIVER_DETAILS: str | None = None

    @abstractmethod
    def __init__(self, conn_str: Sequence[str], **options: Any) -> None:
        """
        Initialize an adapter.

        Args:
            - conn_str (Sequence[str]): One or more connection strings. May be empty.
            - **options (Any): Options received from the command line, config file,
                or env variables. Adapters should be robust to receiving both subsets
                and supersets of their declared options. They should disregard any
                extra (unexpected) kwargs. Adapters should check the types of options,
                as they may not be cast to the correct types.

        Raises: HarlequinConfigError if a received option is the wrong value or type.
        """
        pass

    @abstractmethod
    def connect(self) -> HarlequinConnection:
        """
        Creates and returns an initialized connection to a database. Necessary config
        should be stored in the HarlequinAdapter instance when it is created.

        Returns: HarlequinConnection.

        Raises: HarlequinConnectionError if a connection could not be established.
        """
        pass

    @property
    def connection_id(self) -> str | None:
        """
        Returns a unique ID for this connection, typically a fully-hydrated connection
        string or similar. This Unique ID is used by Harlequin to cache the data
        catalog and query history and persist them across invocations of Harlequin.

        If None is returned, Harlequin will attempt to compute a unique ID from the
        arguments used to initialize the adapter.

        If the empty string is returned, Harlequin will not attempt to load the
        catalog or buffers from the cache.

        Returns: str | None
        """
        return None

    @property
    def implements_copy(self) -> bool:
        """
        True if the adapter's connection implements the copy() method. Adapter must
        also provide options for customizing the Export dialog GUI.
        """
        return self.COPY_FORMATS is not None

    @property
    def provides_details(self) -> bool:
        """
        True if the adapter provides an optional description of itself, visible
        on Harlequin's debugging info screen, NOT including
        driver or connection details, which should be ADAPTER_DRIVER_DETAILS.
        """
        return self.ADAPTER_DETAILS is not None

    @property
    def provides_driver_details(self) -> bool:
        """
        True if the adapter provides an optional description of its driver
        or connection.
        """
        return self.ADAPTER_DRIVER_DETAILS is not None
