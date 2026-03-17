import ipaddress
import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List
from urllib.parse import urlparse

from mdns_helpers.models import AppConfig, SiteConfig, ValidationError


SUPPORTED_DNS_BACKENDS = {"coredns"}
SUPPORTED_PROXY_BACKENDS = {"caddy"}
SUPPORTED_SITE_KINDS = {"static_dir", "upstream"}


def load_config(config_path: str) -> AppConfig:
    path = Path(config_path).expanduser().resolve()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValidationError("config file not found: {0}".format(path)) from exc
    except json.JSONDecodeError as exc:
        raise ValidationError(
            "invalid JSON in config file {0}: {1}".format(path, exc)
        ) from exc

    return app_config_from_dict(raw, path)


def app_config_from_dict(raw: Dict[str, Any], config_path: Path) -> AppConfig:
    domain = _require_string(raw, "domain")
    allow_local_domain = bool(raw.get("allow_local_domain", False))

    host = _require_mapping(raw, "host")
    host_ipv4 = _require_string(host, "ipv4")
    advertise_hostname = str(host.get("advertise_hostname", "homeserver")).strip()

    dns = raw.get("dns", {})
    if dns is None:
        dns = {}
    dns_backend = str(dns.get("backend", "coredns")).strip()
    dns_listen = str(dns.get("listen", "0.0.0.0:53")).strip()
    dns_binary = _optional_string(dns, "binary")
    dns_config_path = _optional_string(dns, "config_path")

    proxy = raw.get("proxy", {})
    if proxy is None:
        proxy = {}
    proxy_backend = str(proxy.get("backend", "caddy")).strip()
    proxy_listen = str(proxy.get("listen", ":80")).strip()
    proxy_binary = _optional_string(proxy, "binary")
    proxy_config_path = _optional_string(proxy, "config_path")

    mdns = raw.get("mdns", {})
    if mdns is None:
        mdns = {}
    mdns_enabled = bool(mdns.get("enabled", False))
    mdns_service_type = str(mdns.get("service_type", "_http._tcp")).strip()

    output_dir_value = raw.get("output_dir", "generated")
    output_dir = _resolve_path(config_path.parent, output_dir_value)

    sites = raw.get("sites")
    if not isinstance(sites, list) or not sites:
        raise ValidationError("config must include a non-empty 'sites' array")

    site_models: List[SiteConfig] = []
    for index, item in enumerate(sites):
        if not isinstance(item, dict):
            raise ValidationError("site entry #{0} must be an object".format(index + 1))
        site_models.append(_site_from_dict(item, config_path.parent, domain))

    app = AppConfig(
        config_path=config_path,
        domain=domain,
        host_ipv4=host_ipv4,
        advertise_hostname=advertise_hostname,
        output_dir=output_dir,
        allow_local_domain=allow_local_domain,
        dns_backend=dns_backend,
        dns_listen=dns_listen,
        dns_binary=dns_binary,
        dns_config_path=dns_config_path,
        proxy_backend=proxy_backend,
        proxy_listen=proxy_listen,
        proxy_binary=proxy_binary,
        proxy_config_path=proxy_config_path,
        mdns_enabled=mdns_enabled,
        mdns_service_type=mdns_service_type,
        sites=site_models,
    )
    validate_config(app)
    return app


def validate_config(config: AppConfig) -> None:
    _validate_domain(config.domain, config.allow_local_domain)
    _validate_ipv4(config.host_ipv4)

    if config.dns_backend not in SUPPORTED_DNS_BACKENDS:
        raise ValidationError(
            "unsupported DNS backend '{0}'; supported: {1}".format(
                config.dns_backend, ", ".join(sorted(SUPPORTED_DNS_BACKENDS))
            )
        )

    if config.proxy_backend not in SUPPORTED_PROXY_BACKENDS:
        raise ValidationError(
            "unsupported proxy backend '{0}'; supported: {1}".format(
                config.proxy_backend, ", ".join(sorted(SUPPORTED_PROXY_BACKENDS))
            )
        )

    names = set()
    hostnames = set()
    for site in config.sites:
        if site.name in names:
            raise ValidationError("duplicate site name '{0}'".format(site.name))
        if site.hostname in hostnames:
            raise ValidationError("duplicate hostname '{0}'".format(site.hostname))
        names.add(site.name)
        hostnames.add(site.hostname)

        if site.kind not in SUPPORTED_SITE_KINDS:
            raise ValidationError(
                "site '{0}' has unsupported kind '{1}'".format(site.name, site.kind)
            )

        if not site.hostname.endswith("." + config.domain):
            raise ValidationError(
                "site '{0}' hostname must end with '.{1}'".format(site.name, config.domain)
            )

        if site.kind == "static_dir":
            if not site.source:
                raise ValidationError(
                    "site '{0}' must define 'source' for static_dir".format(site.name)
                )
            if not os.path.isdir(site.source):
                raise ValidationError(
                    "site '{0}' source directory does not exist: {1}".format(
                        site.name, site.source
                    )
                )
        if site.kind == "upstream":
            if not site.upstream:
                raise ValidationError(
                    "site '{0}' must define 'upstream' for upstream".format(site.name)
                )
            parsed = urlparse(site.upstream)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValidationError(
                    "site '{0}' upstream must be a valid http/https URL".format(site.name)
                )

        if not isinstance(site.index_files, list) or not all(
            isinstance(value, str) and value.strip() for value in site.index_files
        ):
            raise ValidationError(
                "site '{0}' index_files must be a non-empty string list".format(site.name)
            )


def save_config(config: AppConfig) -> None:
    data = json.dumps(config.to_serializable(), indent=2, sort_keys=False)
    temp_path = config.config_path.with_suffix(config.config_path.suffix + ".tmp")
    temp_path.write_text(data + "\n", encoding="utf-8")
    temp_path.replace(config.config_path)


def _site_from_dict(item: Dict[str, Any], base_dir: Path, domain: str) -> SiteConfig:
    name = _require_string(item, "name")
    hostname = str(item.get("hostname", "{0}.{1}".format(name, domain))).strip()
    kind = _require_string(item, "kind")
    enabled = bool(item.get("enabled", True))
    source = _optional_string(item, "source")
    upstream = _optional_string(item, "upstream")
    directory_listing = bool(item.get("directory_listing", False))
    index_files = item.get("index_files", ["index.html", "index.htm"])
    if source:
        source = str(_resolve_path(base_dir, source))

    return SiteConfig(
        name=name,
        hostname=hostname,
        kind=kind,
        enabled=enabled,
        source=source,
        upstream=upstream,
        directory_listing=directory_listing,
        index_files=list(index_files),
    )


def _validate_domain(domain: str, allow_local_domain: bool) -> None:
    if not domain or "." not in domain:
        raise ValidationError("domain must be a dotted name such as 'home.arpa'")
    if domain.endswith(".local") or domain == "local":
        if not allow_local_domain:
            raise ValidationError(
                "'.local' is reserved for mDNS and is rejected by default; "
                "use 'home.arpa' or set allow_local_domain=true"
            )


def _validate_ipv4(value: str) -> None:
    try:
        ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError as exc:
        raise ValidationError("invalid host.ipv4 value '{0}'".format(value)) from exc


def _require_string(mapping: Dict[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("missing or invalid '{0}'".format(key))
    return value.strip()


def _optional_string(mapping: Dict[str, Any], key: str) -> str:
    value = mapping.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("invalid '{0}'".format(key))
    return value.strip()


def _require_mapping(mapping: Dict[str, Any], key: str) -> Dict[str, Any]:
    value = mapping.get(key)
    if not isinstance(value, dict):
        raise ValidationError("missing or invalid '{0}'".format(key))
    return value


def _resolve_path(base_dir: Path, value: Any) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("invalid path value '{0}'".format(value))
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()
