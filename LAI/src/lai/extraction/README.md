# `lai.extraction` — structured extraction

Extracts structured geographic data — addresses, coordinates, parcel/Flurstück IDs —
from document text, for the cadastral / DDiQ workflows.

| Module | Role |
|---|---|
| `location.py` | Location / geo-entity extraction logic. |
| `models.py` | Pydantic models for extracted entities. |
| `repository.py` | Persistence for extraction results. |
| `routes.py` | FastAPI routes for the extraction domain. |

Owner: see [`.github/CODEOWNERS`](../../../../.github/CODEOWNERS).
