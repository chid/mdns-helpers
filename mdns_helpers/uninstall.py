import argparse
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List

from mdns_helpers.config import load_config
from mdns_helpers.generator import (
    SUPPORTED_PLATFORMS,
    _dns_config_path,
    _proxy_config_path,
    detect_platform,
)
from mdns_helpers.models import AppConfig, ValidationError


@dataclass
class ManagedPath:
    path: Path
    kind: str
    label: str


@dataclass
class CommandStep:
    argv: List[str]
    label: str


@dataclass
class UninstallPlan:
    platform_name: str
    generated_paths: List[ManagedPath] = field(default_factory=list)
    deployed_paths: List[ManagedPath] = field(default_factory=list)
    pre_commands: List[CommandStep] = field(default_factory=list)
    post_commands: List[CommandStep] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            "Platform: {0}".format(self.platform_name),
            "Generated paths: {0}".format(len(self.generated_paths)),
            "Deployed paths: {0}".format(len(self.deployed_paths)),
            "Service commands: {0}".format(
                len(self.pre_commands) + len(self.post_commands)
            ),
        ]
        return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="uninstall.py",
        description="Remove mdns-helpers generated artifacts and installed service files.",
    )
    parser.add_argument(
        "-c",
        "--config",
        default="examples/sample-config.json",
        help="Path to the JSON config file.",
    )
    parser.add_argument(
        "--platform",
        default="auto",
        choices=["auto", "macos", "ubuntu"],
        help="Target platform to uninstall.",
    )
    parser.add_argument(
        "--generated-only",
        action="store_true",
        help="Only remove generated files in the repo output directory.",
    )
    parser.add_argument(
        "--deployed-only",
        action="store_true",
        help="Only remove installed system files and stop services.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the uninstall plan without deleting anything.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the confirmation prompt.",
    )
    return parser


def main(argv: Iterable[str] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.generated_only and args.deployed_only:
        parser.error("--generated-only and --deployed-only cannot be used together")

    try:
        config = load_config(args.config)
        platform_name = detect_platform() if args.platform == "auto" else args.platform
        if platform_name not in SUPPORTED_PLATFORMS:
            raise ValidationError(
                "unsupported platform '{0}'; supported: macos, ubuntu".format(
                    platform_name
                )
            )

        plan = plan_uninstall(
            config,
            platform_name,
            remove_generated=not args.deployed_only,
            remove_deployed=not args.generated_only,
        )

        print(plan.summary())
        print("")
        _print_plan(plan)

        if args.dry_run:
            return 0

        if not args.yes and not _confirm():
            print("Aborted.")
            return 1

        execute_uninstall(plan)
        print("")
        print("Uninstall complete.")
        return 0
    except ValidationError as exc:
        print("Error: {0}".format(exc), file=sys.stderr)
        return 2
    except ValueError as exc:
        print("Error: {0}".format(exc), file=sys.stderr)
        return 2


def plan_uninstall(
    config: AppConfig,
    platform_name: str,
    remove_generated: bool = True,
    remove_deployed: bool = True,
) -> UninstallPlan:
    plan = UninstallPlan(platform_name=platform_name)

    if remove_generated:
        generated_root = config.output_dir / platform_name
        plan.generated_paths.append(
            ManagedPath(generated_root, "directory", "generated output root")
        )

    if remove_deployed:
        plan.deployed_paths.extend(_planned_deployed_paths(config, platform_name))
        pre_commands, post_commands = _planned_command_steps(config, platform_name)
        plan.pre_commands.extend(pre_commands)
        plan.post_commands.extend(post_commands)

    return plan


def execute_uninstall(plan: UninstallPlan) -> None:
    for step in plan.pre_commands:
        _run_command(step)

    for item in plan.deployed_paths:
        _remove_path(item)

    for item in plan.generated_paths:
        _remove_path(item)

    for step in plan.post_commands:
        _run_command(step)


def _print_plan(plan: UninstallPlan) -> None:
    if plan.pre_commands:
        print("Service commands before deletion:")
        for step in plan.pre_commands:
            print("- {0}: {1}".format(step.label, " ".join(step.argv)))
        print("")

    if plan.post_commands:
        print("Service commands after deletion:")
        for step in plan.post_commands:
            print("- {0}: {1}".format(step.label, " ".join(step.argv)))
        print("")

    if plan.deployed_paths:
        print("Deployed paths:")
        for item in plan.deployed_paths:
            print("- {0}: {1}".format(item.label, item.path))
        print("")

    if plan.generated_paths:
        print("Generated paths:")
        for item in plan.generated_paths:
            print("- {0}: {1}".format(item.label, item.path))


def _planned_deployed_paths(config: AppConfig, platform_name: str) -> List[ManagedPath]:
    paths = [
        ManagedPath(Path(_dns_config_path(config, platform_name)), "file", "CoreDNS config"),
        ManagedPath(Path(_proxy_config_path(config, platform_name)), "file", "Caddy config"),
    ]

    if platform_name == "ubuntu":
        paths.extend(
            [
                ManagedPath(
                    Path("/etc/systemd/system/coredns.service"),
                    "file",
                    "CoreDNS systemd unit",
                ),
                ManagedPath(
                    Path("/etc/systemd/system/caddy.service"),
                    "file",
                    "Caddy systemd unit",
                ),
            ]
        )
        for site in config.sites:
            paths.append(
                ManagedPath(
                    Path("/etc/avahi/services/{0}.service".format(site.name)),
                    "file",
                    "Avahi service for {0}".format(site.name),
                )
            )
    else:
        paths.extend(
            [
                ManagedPath(
                    Path("/Library/LaunchDaemons/io.charley.coredns.plist"),
                    "file",
                    "CoreDNS launchd plist",
                ),
                ManagedPath(
                    Path("/Library/LaunchDaemons/io.charley.caddy.plist"),
                    "file",
                    "Caddy launchd plist",
                ),
            ]
        )
        for site in config.sites:
            paths.append(
                ManagedPath(
                    Path(
                        "/Library/LaunchDaemons/io.charley.mdns.{0}.plist".format(
                            site.name
                        )
                    ),
                    "file",
                    "mDNS launchd plist for {0}".format(site.name),
                )
            )

    return paths


def _planned_command_steps(config: AppConfig, platform_name: str):
    if platform_name == "ubuntu":
        pre_commands = [
            CommandStep(
                ["systemctl", "disable", "--now", "coredns.service"],
                "stop CoreDNS",
            ),
            CommandStep(
                ["systemctl", "disable", "--now", "caddy.service"],
                "stop Caddy",
            ),
        ]
        post_commands = [CommandStep(["systemctl", "daemon-reload"], "reload systemd")]
        post_commands.append(
            CommandStep(["systemctl", "restart", "avahi-daemon"], "restart Avahi")
        )
        return pre_commands, post_commands

    pre_commands = [
        CommandStep(
            ["launchctl", "unload", "-w", "/Library/LaunchDaemons/io.charley.coredns.plist"],
            "unload CoreDNS",
        ),
        CommandStep(
            ["launchctl", "unload", "-w", "/Library/LaunchDaemons/io.charley.caddy.plist"],
            "unload Caddy",
        ),
    ]
    for site in config.sites:
        pre_commands.append(
            CommandStep(
                [
                    "launchctl",
                    "unload",
                    "-w",
                    "/Library/LaunchDaemons/io.charley.mdns.{0}.plist".format(
                        site.name
                    ),
                ],
                "unload mDNS service for {0}".format(site.name),
            )
        )
    return pre_commands, []


def _run_command(step: CommandStep) -> None:
    try:
        subprocess.run(step.argv, check=False)
    except FileNotFoundError:
        pass


def _remove_path(item: ManagedPath) -> None:
    if item.kind == "directory":
        if item.path.exists():
            shutil.rmtree(item.path)
        _prune_empty_parents(item.path.parent)
        return

    if item.path.exists() or item.path.is_symlink():
        item.path.unlink()
    _prune_empty_parents(item.path.parent)


def _prune_empty_parents(path: Path) -> None:
    while True:
        if not path.exists():
            path = path.parent
            continue
        if path == path.parent:
            return
        try:
            path.rmdir()
        except OSError:
            return
        path = path.parent


def _confirm() -> bool:
    reply = input("Proceed with uninstall? [y/N] ").strip().lower()
    return reply in {"y", "yes"}
