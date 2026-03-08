"""
lai.auth — Authentication and user management domain.

Owns: JWT tokens, password hashing, user CRUD, session management.
Routes: POST /login, POST /register, GET /me
DB: users table, per-user schema creation (user_{uuid})
"""
