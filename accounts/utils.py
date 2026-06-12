"""Utility per le utenze."""

import secrets
import string

_AMBIGUI = set("O0Il1|`'\"")
_SIMBOLI = "!@#$%*?-_"
_POOL = "".join(c for c in (string.ascii_letters + string.digits + _SIMBOLI) if c not in _AMBIGUI)


def genera_password(lunghezza: int = 14) -> str:
    """Password robusta e leggibile (no caratteri ambigui), conforme ai validatori.

    Garantisce minuscola + maiuscola + cifra + simbolo.
    """
    while True:
        pwd = "".join(secrets.choice(_POOL) for _ in range(lunghezza))
        if (any(c.islower() for c in pwd) and any(c.isupper() for c in pwd)
                and any(c.isdigit() for c in pwd) and any(c in _SIMBOLI for c in pwd)):
            return pwd
