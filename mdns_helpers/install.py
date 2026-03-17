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
    generate_artifacts,
    write_artifacts,
)
from mdns_helpers.models import AppConfig, ValidationError


@dataclass
class ManagedCopy:
    source: Path
    destination: Path
    label: str


@dataclass
class CommandStep:
    argv: List[str]
    label: str
    ignore_failure: bool = False


@dataclass
class InstallPlan:
    platform_name: str
    generated_root: Path
    generated_files: List[Path] = field(default_factory=list)
    deployed_copies: List[ManagedCopy] = field(default_factory=list)
    pre_commands: List[CommandStep] = field(default_factory=list)
    post_commands: List[CommandStep] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            "Platform: {0}".format(self.platform_name),
            "Generated root: {0}".format(self.generated_root),
            "Generated files: {0}".format(len(self.generated_files)),
            "Deployed files: {0}".format(len(self.deployed_copies)),
            "Service commands: {0}".format(
                len(self.pre_commands) + len(self.post_commands)
            ),
        ]
        return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="install.py",
        description="Generate and install mdns-helpers artifacts for macOS and Ubuntu.",
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
        help="Target platform to install.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the install plan without copying files or running commands.",
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

    try:
        config = load_config(args.config)
        platform_name = detect_platform() if args.platform == "auto" else args.platform
        if platform_name not in SUPPORTED_PLATFORMS:
            raise ValidationError(
                "unsupported platform '{0}'; supported: macos, ubuntu".format(
                    platform_name
                )
            )

        plan = plan_install(config, platform_name)
        print(plan.summary())
        print("")
        _print_plan(plan)

        if args.dry_run:
            return 0

        if not args.yes and not _confirm():
            print("Aborted.")
            return 1

        execute_install(config, plan)
        print("")
        print("Install complete.")
        return 0
    except ValidationError as exc:
        print("Error: {0}".format(exc), file=sys.stderr)
        return 2
    except ValueError as exc:
        print("Error: {0}".format(exc), file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print("Error: {0}".format(exc), file=sys.stderr)
        return 2


def plan_install(config: AppConfig, platform_name: str) -> InstallPlan:
    artifacts = generate_artifacts(config, platform_name)
    generated_root = config.output_dir / platform_name
    plan = InstallPlan(
        platform_name=platform_name,
        generated_root=generated_root,
        generated_files=sorted(artifacts.files),
    )
    plan.deployed_copies.extend(_planned_deployed_copies(config, platform_name, generated_root))
    pre_commands, post_commands = _planned_command_steps(config, platform_name)
    plan.pre_commands.extend(pre_commands)
    plan.post_commands.extend(post_commands)
    return plan


def execute_install(config: AppConfig, plan: InstallPlan) -> None:
    artifacts = generate_artifacts(config, plan.platform_name)
    write_artifacts(artifacts)

    for item in plan.deployed_copies:
        item.destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item.source, item.destination)

    for step in plan.pre_commands:
        _run_command(step)
    for step in plan.post_commands:
        _run_command(step)


def _print_plan(plan: InstallPlan) -> None:
    print("Generated files:")
    for path in plan.generated_files:
        print("- {0}".format(path))
    print("")

    print("Deployed copies:")
    for item in plan.deployed_copies:
        print("- {0}: {1} -> {2}".format(item.label, item.source, item.destination))

    if plan.pre_commands:
        print("")
        print("Service commands before enable:")
        for step in plan.pre_commands:
            print("- {0}: {1}".format(step.label, " ".join(step.argv)))

    if plan.post_commands:
        print("")
        print("Service commands after copy:")
        for step in plan.post_commands:
            print("- {0}: {1}".format(step.label, " ".join(step.argv)))


def _planned_deployed_copies(
    config: AppConfig, platform_name: str, generated_root: Path
) -> List[ManagedCopy]:
    copies = [
        ManagedCopy(
            generated_root / "dns" / "Corefile",
            Path(_dns_config_path(config, platform_name)),
            "CoreDNS config",
        ),
        ManagedCopy(
            generated_root / "proxy" / "Caddyfile",
            Path(_proxy_config_path(config, platform_name)),
            "Caddy config",
        ),
    ]

    if platform_name == "ubuntu":
        copies.extend(
            [
                ManagedCopy(
                    generated_root / "services" / "coredns.service",
                    Path("/etc/systemd/system/coredns.service"),
                    "CoreDNS systemd unit",
                ),
                ManagedCopy(
                    generated_root / "services" / "caddy.service",
                    Path("/etc/systemd/system/caddy.service"),
                    "Caddy systemd unit",
                ),
            ]
        )
        if config.mdns_enabled:
            for site in config.sites:
                copies.append(
                    ManagedCopy(
                        generated_root / "mdns" / "{0}.service".format(site.name),
                        Path("/etc/avahi/services/{0}.service".format(site.name)),
                        "Avahi service for {0}".format(site.name),
                    )
                )
    else:
        copies.extend(
            [
                ManagedCopy(
                    generated_root / "services" / "io.charley.coredns.plist",
                    Path("/Library/LaunchDaemons/io.charley.coredns.plist"),
                    "CoreDNS launchd plist",
                ),
                ManagedCopy(
                    generated_root / "services" / "io.charley.caddy.plist",
                    Path("/Library/LaunchDaemons/io.charley.caddy.plist"),
                    "Caddy launchd plist",
                ),
            ]
        )
        if config.mdns_enabled:
            for site in config.sites:
                copies.append(
                    ManagedCopy(
                        generated_root / "mdns" / "{0}.plist".format(site.name),
                        Path(
                            "/Library/LaunchDaemons/io.charley.mdns.{0}.plist".format(
                                site.name
                            )
                        ),
                        "mDNS launchd plist for {0}".format(site.name),
                    )
                )

    return copies


def _planned_command_steps(config: AppConfig, platform_name: str):
    if platform_name == "ubuntu":
        pre_commands: List[CommandStep] = []
        post_commands = [
            CommandStep(["systemctl", "daemon-reload"], "reload systemd"),
            CommandStep(
                ["systemctl", "enable", "--now", "coredns.service"],
                "enable CoreDNS",
            ),
            CommandStep(
                ["systemctl", "enable", "--now", "caddy.service"],
                "enable Caddy",
            ),
        ]
        if config.mdns_enabled:
            post_commands.append(
                CommandStep(["systemctl", "restart", "avahi-daemon"], "restart Avahi")
            )
        return pre_commands, post_commands

    pre_commands = [
        CommandStep(
            ["launchctl", "unload", "-w", "/Library/LaunchDaemons/io.charley.coredns.plist"],
            "unload CoreDNS if already loaded",
            ignore_failure=True,
        ),
        CommandStep(
            ["launchctl", "unload", "-w", "/Library/LaunchDaemons/io.charley.caddy.plist"],
            "unload Caddy if already loaded",
            ignore_failure=True,
        ),
    ]
    post_commands = [
        CommandStep(
            ["launchctl", "load", "-w", "/Library/LaunchDaemons/io.charley.coredns.plist"],
            "load CoreDNS",
        ),
        CommandStep(
            ["launchctl", "load", "-w", "/Library/LaunchDaemons/io.charley.caddy.plist"],
            "load Caddy",
        ),
    ]
    if config.mdns_enabled:
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
                    "unload mDNS service for {0} if already loaded".format(site.name),
                    ignore_failure=True,
                )
            )
            post_commands.append(
                CommandStep(
                    [
                        "launchctl",
                        "load",
                        "-w",
                        "/Library/LaunchDaemons/io.charley.mdns.{0}.plist".format(
                            site.name
                        ),
                    ],
                    "load mDNS service for {0}".format(site.name),
                )
            )
    return pre_commands, post_commands


def _run_command(step: CommandStep) -> None:
    try:
        subprocess.run(step.argv, check=not step.ignore_failure)
    except FileNotFoundError as exc:
        if step.ignore_failure:
            return
        raise RuntimeError(
            "command not found while running '{0}': {1}".format(step.label, step.argv[0])
        ) from exc
    except subprocess.CalledProcessError as exc:
        if step.ignore_failure:
            return
        raise RuntimeError(
            "command failed for '{0}' with exit code {1}".format(
                step.label, exc.returncode
            )
        ) from exc


def _confirm() -> bool:
    reply = input("Proceed with install? [y/N] ").strip().lower()
    return reply in {"y", "yes"}
