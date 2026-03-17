import argparse
import sys
from typing import Iterable

from mdns_helpers.config import load_config, save_config
from mdns_helpers.generator import generate_artifacts, write_artifacts
from mdns_helpers.models import ValidationError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mdns-helpers",
        description="Generate and maintain local DNS/proxy/mDNS artifacts for macOS and Ubuntu.",
    )
    parser.add_argument(
        "-c",
        "--config",
        default="examples/sample-config.json",
        help="Path to the JSON config file.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("validate", help="Validate the config.")

    plan = subparsers.add_parser("plan", help="Show what would be generated.")
    plan.add_argument(
        "--platform",
        default="auto",
        choices=["auto", "macos", "ubuntu"],
        help="Target platform for generated artifacts.",
    )

    apply_cmd = subparsers.add_parser("apply", help="Generate deployment artifacts.")
    apply_cmd.add_argument(
        "--platform",
        default="auto",
        choices=["auto", "macos", "ubuntu"],
        help="Target platform for generated artifacts.",
    )

    subparsers.add_parser("list", help="List configured sites.")

    enable = subparsers.add_parser("enable", help="Enable a site in the config.")
    enable.add_argument("site_name", help="Configured site name.")

    disable = subparsers.add_parser("disable", help="Disable a site in the config.")
    disable.add_argument("site_name", help="Configured site name.")

    return parser


def main(argv: Iterable[str] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        config = load_config(args.config)
        if args.command == "validate":
            print(
                "Config is valid for domain {0} with {1} site(s).".format(
                    config.domain, len(config.sites)
                )
            )
            return 0

        if args.command == "plan":
            artifacts = generate_artifacts(config, args.platform)
            print(artifacts.summary)
            print("")
            print("Files that would be written:")
            for path in sorted(artifacts.files):
                print("- {0}".format(path))
            return 0

        if args.command == "apply":
            artifacts = generate_artifacts(config, args.platform)
            written = write_artifacts(artifacts)
            print(artifacts.summary)
            print("")
            print("Wrote {0} file(s):".format(len(written)))
            for path in written:
                print("- {0}".format(path))
            return 0

        if args.command == "list":
            for site in config.sites:
                target = site.source if site.kind == "static_dir" else site.upstream
                status = "enabled" if site.enabled else "disabled"
                print(
                    "{0}\t{1}\t{2}\t{3}".format(
                        site.name, site.hostname, site.kind, status
                    )
                )
                print("  target: {0}".format(target))
            return 0

        if args.command == "enable":
            return _toggle_site(config, args.site_name, enabled=True)
        if args.command == "disable":
            return _toggle_site(config, args.site_name, enabled=False)
    except ValidationError as exc:
        print("Error: {0}".format(exc), file=sys.stderr)
        return 2
    except ValueError as exc:
        print("Error: {0}".format(exc), file=sys.stderr)
        return 2

    parser.error("unknown command")
    return 2


def _toggle_site(config, site_name: str, enabled: bool) -> int:
    for site in config.sites:
        if site.name == site_name:
            site.enabled = enabled
            save_config(config)
            verb = "Enabled" if enabled else "Disabled"
            print("{0} site '{1}' in {2}".format(verb, site_name, config.config_path))
            return 0
    raise ValidationError("site '{0}' not found".format(site_name))
