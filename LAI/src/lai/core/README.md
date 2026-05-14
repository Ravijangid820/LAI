# `lai.core` — core / shared

Foundational code imported across every other package. Keep this dependency-light —
nothing here should import from sibling domains.

| Module | Role |
|---|---|
| `config.py` | Settings / environment configuration. |
| `constants.py` | Project-wide constants. |
| `logging.py` | Logging setup. |
| `models.py` | Shared data models. |
| `exceptions.py` | Shared exception types. |
| `utils.py` | Generic helpers. |

Owner: see [`.github/CODEOWNERS`](../../../../.github/CODEOWNERS).
