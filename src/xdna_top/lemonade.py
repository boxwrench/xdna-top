import sys

from xdna_top.main import _run_monitor_with_theme, build_parser


def main() -> int:
    parser = build_parser(
        "lemonade-top: Lemonade-themed NPU+iGPU Telemetry Monitor",
        include_commands=False,
    )
    args = parser.parse_args()
    return _run_monitor_with_theme(args, default_theme_name="lemonade")


if __name__ == "__main__":
    sys.exit(main())
