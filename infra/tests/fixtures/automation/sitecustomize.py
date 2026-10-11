"""Deny network access in the isolated Python automation fixture."""

import os
import socket

if os.environ.get("SOLO_EMPIRE_AUTOMATION_FIXTURE"):
    def deny_network(*args, **kwargs):
        raise PermissionError("Network access is disabled in automation fixtures")

    socket.socket.connect = deny_network
    socket.socket.connect_ex = deny_network
    socket.create_connection = deny_network
    socket.getaddrinfo = deny_network

    # Chain the SQLite store doubles (tests/support/sitecustomize.py): only one sitecustomize is imported.
    _doubles = os.path.join(os.environ["SOLO_EMPIRE_AUTOMATION_FIXTURE"], "infra", "tests", "support", "sitecustomize.py")
    if os.path.isfile(_doubles):
        with open(_doubles, encoding="utf-8") as _handle:
            exec(compile(_handle.read(), _doubles, "exec"), {"__file__": _doubles, "__name__": "sitecustomize_doubles"})
