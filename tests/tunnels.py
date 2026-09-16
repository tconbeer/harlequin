"""What the two fake-`ssh` test modules share about a tunnel that will not open."""

from __future__ import annotations

from harlequin.exception import HarlequinSshError


def ssh_child_exited(error: BaseException) -> bool:
    """Whether `start()` failed because the child exited, rather than timing out.

    Losing a bind race looks like this: `ExitOnForwardFailure=yes` makes a
    child that cannot take its port exit, so the port is worth retrying. A
    readiness timeout is `SshTunnel.timeout` long and means something else, so
    it is the one failure a retry must not multiply.
    """
    return isinstance(error, HarlequinSshError) and "exited with code" in str(error)
