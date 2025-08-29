#!/usr/bin/env python
from app.core.state_store import cleanup_expired

n = cleanup_expired()
print(f"[state-store] cleaned {n} expired entries")
