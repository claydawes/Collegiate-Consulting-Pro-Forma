"""The shared-password gate in front of the platform.

The password lives in Streamlit's secrets, never in this repository. On
Streamlit Community Cloud it goes in the app's **Settings -> Secrets**;
running locally it goes in `.streamlit/secrets.toml`, which is in .gitignore.
Either way the value is:

    [auth]
    password = "the-shared-password"

`.streamlit/secrets.toml.example` carries the shape with a placeholder, so
the repository documents the setting without ever containing the secret.

**Fails closed.** If no password is configured the app refuses everyone and
says so, rather than falling open. A misconfiguration that silently unlocked
a tool holding client financial data would be worse than an outage.

What this is and isn't: one password shared by the team is a real barrier -
someone who finds the URL cannot get in - but it is a single secret with no
per-person identity behind it. It cannot tell you who used it, and it stops
protecting anything the moment it is forwarded outside the team. Rotating it
in the Secrets panel is what recovers from a leak.

If you later want to know *who* signed in, that needs per-person identity
rather than a shared secret: Streamlit Cloud's Settings -> Sharing viewer
allowlist, or st.login() against Google Workspace / Microsoft Entra. Both
can sit in front of this.
"""

from __future__ import annotations

import hmac

#: Where the password sits in the secrets file.
_SECTION, _KEY = "auth", "password"


class PasswordNotConfigured(RuntimeError):
    """No password is set in secrets, so nobody can be let in."""


def configured_password(secrets) -> str:
    """The configured password, or PasswordNotConfigured.

    `secrets` is st.secrets. Reading it raises if there's no secrets file at
    all, which is the same failure as an empty one from this function's point
    of view.
    """
    try:
        section = secrets[_SECTION]
        value = section[_KEY]
    except Exception as exc:  # noqa: BLE001 - any lookup failure means "not set"
        raise PasswordNotConfigured(
            "No password is configured. Add an [auth] section with a "
            "'password' key to the app's Secrets."
        ) from exc
    if not isinstance(value, str) or not value:
        raise PasswordNotConfigured("The configured password is empty.")
    return value


def verify(candidate: str, expected: str) -> bool:
    """Constant-time comparison, so the time taken to reject a guess doesn't
    leak how much of it was right.
    """
    return hmac.compare_digest(candidate.encode("utf-8"), expected.encode("utf-8"))
