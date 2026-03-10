#!/usr/bin/env python3
"""Desktop entrypoint for HashWatcher Gateway."""

from gateway.hub_agent import HubAgent


def main() -> None:
    HubAgent().run()


if __name__ == "__main__":
    main()

