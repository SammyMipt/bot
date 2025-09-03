#!/usr/bin/env python
from app.core.state_store import cleanup_expired


def main() -> None:
    n = cleanup_expired()
    print(f"[state-store] cleaned {n} expired entries")


if __name__ == "__main__":
    main()
