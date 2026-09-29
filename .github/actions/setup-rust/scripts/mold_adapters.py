"""Side-effect adapters for setup-rust's mold installer.

``install_mold.py`` decides; these two functions are the only places it
touches a process or the network. Each is a narrow callable the installer
takes as a parameter, so its tests substitute a stand-in rather than patching
the standard library, and a new failure mode of either boundary has one place
to be handled.

Standard library only: the installer runs on the caller's runner before any
dependency is installed.
"""

from __future__ import annotations

import subprocess
import typing as typ
import urllib.request

if typ.TYPE_CHECKING:
    import collections.abc as cabc

#: Runs *argv* with a timeout in seconds and returns the completed process.
RunBinary = typ.Callable[
    ["cabc.Sequence[str]", float], "subprocess.CompletedProcess[str]"
]
#: Opens *url* with a timeout in seconds and returns a readable response.
OpenUrl = typ.Callable[[str, float], typ.ContextManager[typ.BinaryIO]]


def run_binary(
    argv: cabc.Sequence[str], timeout: float
) -> subprocess.CompletedProcess[str]:
    """Run *argv*, capturing text output, and return it whatever its status.

    Raises
    ------
    OSError
        If the binary cannot be executed.
    subprocess.TimeoutExpired
        If it runs past *timeout* seconds.
    """
    return subprocess.run(  # noqa: S603, TID251 - stdlib only; runs the binary the installer placed.
        list(argv), check=False, capture_output=True, text=True, timeout=timeout
    )


def open_url(url: str, timeout: float) -> typ.ContextManager[typ.BinaryIO]:
    """Open *url* for reading, following redirects, with *timeout* seconds.

    Raises
    ------
    OSError
        If the connection or request fails, including HTTP error statuses.
    """
    return urllib.request.urlopen(url, timeout=timeout)  # noqa: S310 - the release base URL's scheme.
