from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


class ValidationError(Exception):
    """Raised when the configuration is invalid."""


@dataclass
class SiteConfig:
    name: str
    hostname: str
    kind: str
    enabled: bool = True
    source: Optional[str] = None
    upstream: Optional[str] = None
    directory_listing: bool = False
    index_files: List[str] = field(default_factory=lambda: ["index.html", "index.htm"])


@dataclass
class AppConfig:
    config_path: Path
    domain: str
    host_ipv4: str
    advertise_hostname: str
    output_dir: Path
    allow_local_domain: bool = False
    dns_backend: str = "coredns"
    dns_listen: str = "0.0.0.0:53"
    dns_binary: Optional[str] = None
    dns_config_path: Optional[str] = None
    proxy_backend: str = "caddy"
    proxy_listen: str = ":80"
    proxy_binary: Optional[str] = None
    proxy_config_path: Optional[str] = None
    mdns_enabled: bool = False
    mdns_service_type: str = "_http._tcp"
    sites: List[SiteConfig] = field(default_factory=list)

    def to_serializable(self) -> Dict[str, object]:
        return {
            "domain": self.domain,
            "allow_local_domain": self.allow_local_domain,
            "host": {
                "ipv4": self.host_ipv4,
                "advertise_hostname": self.advertise_hostname,
            },
            "output_dir": str(self.output_dir),
            "dns": {
                "backend": self.dns_backend,
                "listen": self.dns_listen,
                "binary": self.dns_binary,
                "config_path": self.dns_config_path,
            },
            "proxy": {
                "backend": self.proxy_backend,
                "listen": self.proxy_listen,
                "binary": self.proxy_binary,
                "config_path": self.proxy_config_path,
            },
            "mdns": {
                "enabled": self.mdns_enabled,
                "service_type": self.mdns_service_type,
            },
            "sites": [
                {
                    "name": site.name,
                    "hostname": site.hostname,
                    "kind": site.kind,
                    "enabled": site.enabled,
                    "source": site.source,
                    "upstream": site.upstream,
                    "directory_listing": site.directory_listing,
                    "index_files": site.index_files,
                }
                for site in self.sites
            ],
        }
