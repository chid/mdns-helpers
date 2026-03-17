# mdns-helpers

`mdns-helpers` generates and maintains the files needed to expose local websites such as `movies.home.arpa` on macOS and Ubuntu. It is designed for the case where a folder or local app should be reachable from your laptop and from other devices on the same LAN.

The tool treats DNS as the canonical resolution layer and mDNS as optional service discovery. That distinction matters:

- Use `home.arpa` for the actual site names you type into a browser.
- Avoid `.local` as the canonical zone because it is reserved for mDNS and behaves inconsistently across clients.
- Use Bonjour/Avahi only as an optional way to advertise `_http._tcp` services on the LAN.

## How it works

The workflow is declarative:

1. Define sites in a JSON config file.
2. Run `validate` to catch broken paths, duplicate hostnames, invalid upstream URLs, or unsafe domain choices.
3. Run `plan` to preview the generated artifacts.
4. Run `apply` to write deployment-ready files into `generated/macos/` or `generated/ubuntu/`.
5. Copy the generated files to the platform's real config paths and install the generated service definitions.

Generated outputs include:

- A CoreDNS `Corefile` that maps your managed hostnames to one machine IP.
- A Caddy `Caddyfile` that routes each hostname to a static directory or upstream app.
- Platform-specific service definitions for CoreDNS and Caddy.
- Optional mDNS advertisement definitions:
  - Ubuntu: Avahi `.service` files.
  - macOS: `launchd` plists that run `dns-sd -R`.
- An inventory JSON and generated deployment notes.

## Requirements

- Python 3.9+
- CoreDNS installed on the host that should answer LAN DNS requests
- Caddy installed on the same host
- Ubuntu only: Avahi if you want mDNS advertisement
- macOS only: `dns-sd` is built in

This repository has no Python package dependencies.

## Quickstart

Validate the included sample config:

```bash
python3 -m mdns_helpers -c examples/sample-config.json validate
```

Preview the Ubuntu deployment files:

```bash
python3 -m mdns_helpers -c examples/sample-config.json plan --platform ubuntu
```

Generate the files:

```bash
python3 -m mdns_helpers -c examples/sample-config.json apply --platform macos
python3 -m mdns_helpers -c examples/sample-config.json apply --platform ubuntu
```

The generated files are written under `generated/<platform>/`.

## Example config

The sample config at [examples/sample-config.json](/Users/charley/codex/mdns-helpers/examples/sample-config.json) defines:

- `movies.home.arpa` as a static directory served from [examples/movies](/Users/charley/codex/mdns-helpers/examples/movies)
- `radarr.home.arpa` as a reverse proxy to `http://127.0.0.1:7878`

Config shape:

```json
{
  "domain": "home.arpa",
  "host": {
    "ipv4": "192.168.1.50",
    "advertise_hostname": "homeserver"
  },
  "output_dir": "../generated",
  "dns": {
    "backend": "coredns",
    "listen": "0.0.0.0:53",
    "binary": "/opt/homebrew/bin/coredns"
  },
  "proxy": {
    "backend": "caddy",
    "listen": ":80",
    "binary": "/opt/homebrew/bin/caddy"
  },
  "mdns": {
    "enabled": true,
    "service_type": "_http._tcp"
  },
  "sites": [
    {
      "name": "movies",
      "hostname": "movies.home.arpa",
      "kind": "static_dir",
      "source": "./movies",
      "enabled": true,
      "directory_listing": true
    },
    {
      "name": "radarr",
      "hostname": "radarr.home.arpa",
      "kind": "upstream",
      "upstream": "http://127.0.0.1:7878",
      "enabled": true
    }
  ]
}
```

Notes:

- `source` paths are resolved relative to the config file.
- If `hostname` is omitted, it defaults to `<name>.<domain>`.
- `.local` is rejected by default. You can set `allow_local_domain` to `true`, but that is a compatibility escape hatch, not the recommended setup.

## CLI commands

Validate config:

```bash
python3 -m mdns_helpers -c path/to/config.json validate
```

Preview generated files:

```bash
python3 -m mdns_helpers -c path/to/config.json plan --platform macos
python3 -m mdns_helpers -c path/to/config.json plan --platform ubuntu
```

Write generated files:

```bash
python3 -m mdns_helpers -c path/to/config.json apply --platform macos
python3 -m mdns_helpers -c path/to/config.json apply --platform ubuntu
```

List sites:

```bash
python3 -m mdns_helpers -c path/to/config.json list
```

Disable or re-enable a site in the config:

```bash
python3 -m mdns_helpers -c path/to/config.json disable movies
python3 -m mdns_helpers -c path/to/config.json enable movies
```

Preview an uninstall without deleting anything:

```bash
python3 scripts/uninstall.py -c path/to/config.json --platform macos --dry-run
python3 scripts/uninstall.py -c path/to/config.json --platform ubuntu --dry-run
```

## Deploying on macOS

`apply --platform macos` generates:

- `generated/macos/dns/Corefile`
- `generated/macos/proxy/Caddyfile`
- `generated/macos/services/io.charley.coredns.plist`
- `generated/macos/services/io.charley.caddy.plist`
- `generated/macos/mdns/*.plist` when mDNS is enabled

Recommended deployment flow:

1. Install CoreDNS and Caddy.
2. Copy the generated `Corefile` to `/usr/local/etc/coredns/Corefile`.
3. Copy the generated `Caddyfile` to `/usr/local/etc/caddy/Caddyfile`.
4. Adjust binary paths in the generated plists if Homebrew installed to a different location.
5. Load the plists with `launchctl`.
6. Point your router or client DNS settings at the host machine's LAN IP.

Example:

```bash
sudo cp generated/macos/dns/Corefile /usr/local/etc/coredns/Corefile
sudo cp generated/macos/proxy/Caddyfile /usr/local/etc/caddy/Caddyfile
sudo cp generated/macos/services/io.charley.coredns.plist /Library/LaunchDaemons/
sudo cp generated/macos/services/io.charley.caddy.plist /Library/LaunchDaemons/
sudo launchctl load -w /Library/LaunchDaemons/io.charley.coredns.plist
sudo launchctl load -w /Library/LaunchDaemons/io.charley.caddy.plist
```

If mDNS advertisement is enabled, copy the generated `generated/macos/mdns/*.plist` files into `/Library/LaunchDaemons/` and load them the same way.

## Deploying on Ubuntu

`apply --platform ubuntu` generates:

- `generated/ubuntu/dns/Corefile`
- `generated/ubuntu/proxy/Caddyfile`
- `generated/ubuntu/services/coredns.service`
- `generated/ubuntu/services/caddy.service`
- `generated/ubuntu/mdns/*.service` when mDNS is enabled

Recommended deployment flow:

1. Install CoreDNS and Caddy.
2. Copy the generated `Corefile` to `/etc/coredns/Corefile`.
3. Copy the generated `Caddyfile` to `/etc/caddy/Caddyfile`.
4. Install the generated `systemd` units into `/etc/systemd/system/`.
5. If mDNS is enabled, copy the generated Avahi service files into `/etc/avahi/services/`.
6. Reload `systemd`, enable the services, and point your router or clients at this host for DNS.

Example:

```bash
sudo cp generated/ubuntu/dns/Corefile /etc/coredns/Corefile
sudo cp generated/ubuntu/proxy/Caddyfile /etc/caddy/Caddyfile
sudo cp generated/ubuntu/services/coredns.service /etc/systemd/system/
sudo cp generated/ubuntu/services/caddy.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now coredns.service caddy.service
```

If Avahi is enabled:

```bash
sudo cp generated/ubuntu/mdns/*.service /etc/avahi/services/
sudo systemctl restart avahi-daemon
```

## Uninstalling

The uninstall script removes files managed by this repo:

- generated output under `generated/<platform>/`
- deployed CoreDNS and Caddy config files
- deployed `launchd`, `systemd`, and Avahi service files

It does not uninstall the CoreDNS or Caddy binaries themselves.

Preview the uninstall plan:

```bash
python3 scripts/uninstall.py -c examples/sample-config.json --platform macos --dry-run
python3 scripts/uninstall.py -c examples/sample-config.json --platform ubuntu --dry-run
```

Run the uninstall:

```bash
python3 scripts/uninstall.py -c examples/sample-config.json --platform macos --yes
python3 scripts/uninstall.py -c examples/sample-config.json --platform ubuntu --yes
```

Useful flags:

- `--generated-only` removes only repo-generated files.
- `--deployed-only` removes only installed system files and stops services.
- `--yes` skips the confirmation prompt.

## Making LAN resolution work

Generating files is only half of the setup. Other devices on the LAN must actually query your CoreDNS host. Use one of these approaches:

- Best: configure your router's DHCP settings to advertise the CoreDNS host as the LAN DNS server.
- Acceptable: manually point selected devices to the CoreDNS host.
- Not recommended: rely on mDNS alone for browser hostnames.

If the DNS server is only configured on your Mac or Ubuntu host, only that machine will resolve `*.home.arpa`.

## Troubleshooting

`movies.home.arpa` does not resolve:

- Check that CoreDNS is listening on the host IP and port 53.
- Confirm the client is using the CoreDNS host as its DNS server.
- Confirm the hostname appears in `generated/<platform>/inventory/sites.json`.

The site resolves but does not load:

- Check that Caddy is running.
- For `static_dir` sites, check that the source directory exists.
- For `upstream` sites, check the target app on `127.0.0.1` or the configured upstream URL.

`.local` behaves strangely:

- That is expected on mixed networks. Switch to `home.arpa` for canonical names.

Bonjour/Avahi discovery does not appear:

- On Ubuntu, verify Avahi is installed and the generated `.service` files were copied into `/etc/avahi/services/`.
- On macOS, verify the generated `dns-sd` plists are loaded.

## Development

Run the test suite:

```bash
python3 -m unittest discover -s tests
```
