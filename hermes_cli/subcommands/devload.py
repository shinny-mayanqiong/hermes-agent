"""``hermes devload`` subcommand parser."""

from __future__ import annotations

from typing import Callable


def build_devload_parser(subparsers, *, cmd_devload: Callable) -> None:
    """Attach the ``devload`` subcommand to ``subparsers``."""

    parser = subparsers.add_parser(
        "devload",
        help="Show load for configured development machines",
        description=(
            "Query development machines declared in config.yaml under "
            "dev_machines and report current load, CPU count, and memory use."
        ),
    )
    parser.add_argument(
        "machines",
        nargs="*",
        metavar="MACHINE",
        help="Configured machine name. With no names, all machines are queried.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Query all configured machines explicitly.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Print structured JSON for scripts and skills.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Override SSH/probe timeout for every machine.",
    )
    parser.set_defaults(func=cmd_devload)
