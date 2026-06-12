import sys

from xdna_top.main import LEMONADE_THEME, build_parser, run_monitor


def main() -> int:
    parser = build_parser(
        "lemonade-top: Lemonade-themed NPU+iGPU Telemetry Monitor",
        include_commands=False,
    )
    args = parser.parse_args()
    return run_monitor(args, theme=LEMONADE_THEME)


if __name__ == "__main__":
    sys.exit(main())
