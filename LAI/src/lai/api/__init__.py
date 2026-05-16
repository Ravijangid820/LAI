"""LAI HTTP entry points.

This package now hosts a single runtime application: ``serve_rag``, the
chat + contract-analyzer FastAPI on port 18000. The earlier multi-router
shell (``main.py``, ``pipeline.py``) and its dead-stack dependencies
(``lai.auth``, ``lai.documents``, ``lai.extraction``, ``lai.generation``,
``lai.infra``) were removed during the v1 demo restructure. The active
v1 surface lives in :mod:`lai.api.serve_rag`; new shared primitives go
into :mod:`lai.common`.
"""
