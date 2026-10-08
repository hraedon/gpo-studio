#!/usr/bin/env python3
"""Build a deterministic, controller-owned firewall two-leg candidate (WI-025)."""

from __future__ import annotations

import argparse
import base64
import hashlib
import ipaddress
import json
import sys
from dataclasses import asdict
from pathlib import Path

from gpo_studio.export import gpmc_backup_bundle, native_backup_id
from gpo_studio.firewall_policy import (
    FirewallPolicy,
    FirewallProfileSettings,
    FirewallRule,
    to_registry_settings,
)
from gpo_studio.model import GPO, ValidationError
from gpo_studio.registry_pol import serialize
from gpo_studio.validation import validate_gpo

ARCHIVE_NAME = "studio-firewall-backup.zip"
REQUIRED_CANDIDATE_FILES = (ARCHIVE_NAME, "authoring.json", "expected.json")


def candidate_policy() -> FirewallPolicy:
    rules = (
        FirewallRule(
            "StudioFwLane-01",
            "StudioFwLane in tcp allow",
            protocol=6,
            profiles=("domain",),
            local_port="65001",
        ),
        FirewallRule(
            "StudioFwLane-02",
            "StudioFwLane out udp block range",
            direction="outbound",
            action="block",
            protocol=17,
            profiles=("domain", "private"),
            remote_port_range=(65002, 65003),
            remote_port=65010,
        ),
        FirewallRule("StudioFwLane-03", "StudioFwLane icmpv4 echo", protocol=1, icmp4="8:0"),
        FirewallRule("StudioFwLane-04", "StudioFwLane icmpv6 any", protocol=58),
        FirewallRule(
            "StudioFwLane-05",
            "StudioFwLane any protocol addresses",
            action="block",
            local_address="198.51.100.7",
            remote_addresses=("192.0.2.0/255.255.255.0", "2001:db8::/32"),
        ),
        FirewallRule(
            "StudioFwLane-06",
            "StudioFwLane program",
            direction="outbound",
            protocol=6,
            remote_port=65011,
            program=r"%ProgramFiles%\StudioFwLane\probe.exe",
        ),
        FirewallRule(
            "StudioFwLane-07",
            "StudioFwLane service",
            protocol=6,
            local_port="65004",
            service="StudioFwLaneSvc",
        ),
        FirewallRule(
            "StudioFwLane-08",
            "StudioFwLane rpc keyword",
            protocol=6,
            local_port="RPC",
            remote_addresses=("LocalSubnet",),
        ),
        FirewallRule(
            "StudioFwLane-09", "StudioFwLane rpc epmap", protocol=6, local_port="RPC-EPMap"
        ),
        FirewallRule(
            "StudioFwLane-10",
            "StudioFwLane disabled grouped",
            protocol=6,
            local_port="65005",
            enabled=False,
            description="synthetic probe rule",
            group="StudioFwLane Group",
        ),
        FirewallRule(
            "StudioFwLane-11",
            "StudioFwLane edge and interface",
            protocol=17,
            local_port="65006",
            edge_traversal=True,
            interface_type="Lan",
        ),
        FirewallRule(
            "StudioFwLane-12",
            "StudioFwLane protocol number",
            direction="outbound",
            action="block",
            protocol=47,
        ),
        FirewallRule(
            "StudioFwLane-13",
            "StudioFwLane override block",
            protocol=6,
            action="bypass",
            local_port="65007",
            remote_machine="D:(A;;CC;;;WD)",
            security="Authenticate",
        ),
    )
    return FirewallPolicy(
        policy_version=545,
        domain=FirewallProfileSettings(
            enabled=True,
            default_inbound_action="block",
            default_outbound_action="allow",
            log_dropped_packets=True,
            log_successful_connections=False,
            log_file_size_kb=8192,
            log_file_path=r"%systemroot%\system32\LogFiles\Firewall\StudioFwLane-domain.log",
        ),
        private=FirewallProfileSettings(
            enabled=True,
            default_inbound_action="block",
            disable_notifications=True,
            log_successful_connections=True,
        ),
        public=FirewallProfileSettings(),
        rules=rules,
    )


def authoring(policy: FirewallPolicy) -> dict[str, object]:
    rules = []
    for r in policy.rules:
        p: dict[str, object] = {
            "Name": r.rule_id,
            "DisplayName": r.name,
            "Direction": r.direction.title(),
            "Action": "Allow" if r.action == "bypass" else r.action.title(),
            "Enabled": str(r.enabled),
            "Profile": [x.title() for x in r.profiles] or ["Any"],
        }
        if r.protocol is not None:
            p["Protocol"] = {6: "TCP", 17: "UDP", 1: "ICMPv4", 58: "ICMPv6", 47: "47"}[r.protocol]
        for field, value in (
            ("LocalPort", "RPCEPMap" if r.local_port == "RPC-EPMap" else r.local_port),
            ("IcmpType", r.icmp4),
            ("LocalAddress", r.local_address),
            ("Program", r.program),
            ("Service", r.service),
            ("Description", r.description),
            ("Group", r.group),
            ("InterfaceType", "Wired" if r.interface_type == "Lan" else None),
            ("EdgeTraversalPolicy", "Allow" if r.edge_traversal else None),
        ):
            if value is not None:
                p[field] = value
        if r.remote_addresses:
            p["RemoteAddress"] = [
                str(ipaddress.ip_network(x)) if x != "LocalSubnet" else x
                for x in r.remote_addresses
            ]
        ports = []
        if r.remote_port_range:
            ports.append("-".join(map(str, r.remote_port_range)))
        if r.remote_port:
            ports.append(str(r.remote_port))
        if ports:
            p["RemotePort"] = ports
        if r.action == "bypass":
            p.update(
                Authentication="Required", OverrideBlockRules=True, RemoteMachine=r.remote_machine
            )
        rules.append(p)
    profiles = []
    for name, profile in [("Domain", policy.domain), ("Private", policy.private)]:
        p = {"Name": name}
        for field, value in (
            ("Enabled", profile.enabled),
            ("DefaultInboundAction", profile.default_inbound_action),
            ("DefaultOutboundAction", profile.default_outbound_action),
            ("LogBlocked", profile.log_dropped_packets),
            ("LogAllowed", profile.log_successful_connections),
            ("LogMaxSizeKilobytes", profile.log_file_size_kb),
            ("LogFileName", profile.log_file_path),
            (
                "NotifyOnListen",
                None
                if profile.disable_notifications is None
                else not profile.disable_notifications,
            ),
        ):
            if value is not None:
                p[field] = (
                    value.title()
                    if isinstance(value, str) and field.startswith("Default")
                    else str(value)
                    if type(value) is bool
                    else value
                )
        profiles.append(p)
    return {"schema_version": 1, "rules": rules, "profiles": profiles}


def expectation(policy: FirewallPolicy, gpo: GPO) -> dict[str, object]:
    authored = authoring(policy)
    rules = []
    for p in authored["rules"]:
        row = dict(
            name=p["Name"],
            display_name=p["DisplayName"],
            description=p.get("Description", ""),
            group=p.get("Group", ""),
            enabled=p["Enabled"],
            direction=p["Direction"],
            action=p["Action"],
            profile=", ".join(p["Profile"]),
            edge_traversal=p.get("EdgeTraversalPolicy", "Block"),
            protocol=p.get("Protocol", "Any"),
            local_port=p.get("LocalPort", "Any"),
            remote_port=";".join(p.get("RemotePort", ["Any"])),
            icmp_type=p.get("IcmpType", "Any"),
            local_address=p.get("LocalAddress", "Any"),
            remote_address=";".join(p.get("RemoteAddress", ["Any"])),
            program=p.get("Program", "Any"),
            service=p.get("Service", "Any"),
            interface_type=p.get("InterfaceType", "Any"),
            interface_alias="Any",
            authentication=p.get("Authentication", "NotRequired"),
            encryption="NotRequired",
            override_block_rules=str(p.get("OverrideBlockRules", False)),
            remote_machine=p.get("RemoteMachine", ""),
        )
        # These are measured cmdlet facts, not a generic permissive normalizer.
        if p["Name"] == "StudioFwLane-03":
            row["local_port"] = "RPC"
        if p["Name"] == "StudioFwLane-05":
            row["remote_address"] = "192.0.2.0/255.255.255.0;2001:db8::/32"
        rules.append(row)
    fields = {
        "enabled": "Enabled",
        "default_inbound": "DefaultInboundAction",
        "default_outbound": "DefaultOutboundAction",
        "log_allowed": "LogAllowed",
        "log_blocked": "LogBlocked",
        "log_ignored": "LogIgnored",
        "log_file": "LogFileName",
        "log_max_kb": "LogMaxSizeKilobytes",
        "notify_on_listen": "NotifyOnListen",
        "allow_local_rules": "AllowLocalFirewallRules",
    }
    profiles = []
    for p in [*authored["profiles"], {"Name": "Public"}]:
        profiles.append(
            {"name": p["Name"], **{k: str(p.get(v, "NotConfigured")) for k, v in fields.items()}}
        )
    normalization_specs = [
        ("icmpv4-port-filter-rpc", "03", "ICMP4=8:0; no LPort", {"local_port": "RPC"}),
        ("rpc-endpoint-map-spelling", "09", "LPort=RPC-EPMap", {"local_port": "RPCEPMap"}),
        ("wired-interface-spelling", "11", "IFType=Lan", {"interface_type": "Wired"}),
        (
            "authenticated-bypass-action",
            "13",
            "Action=ByPass; Security=Authenticate",
            {
                "action": "Allow",
                "authentication": "Required",
                "override_block_rules": "True",
                "remote_machine": "D:(A;;CC;;;WD)",
            },
        ),
        (
            "ipv4-subnet-mask-form",
            "05",
            "RA4=192.0.2.0/255.255.255.0 (authored /24)",
            {"remote_address": "192.0.2.0/255.255.255.0;2001:db8::/32"},
        ),
        (
            "local-subnet-family-expansion",
            "08",
            "RA4=LocalSubnet; RA6=LocalSubnet",
            {"remote_address": "LocalSubnet"},
        ),
    ]
    return {
        "schema_version": 1,
        "policy": asdict(policy),
        "backup_id": native_backup_id(gpo),
        "rules_readback": rules,
        "profiles_readback": profiles,
        "normalizations": [
            {"name": name, "rule_id": "StudioFwLane-" + number, "wire": wire, "readback": readback}
            for name, number, wire, readback in normalization_specs
        ],
        "registry_records": [
            {
                "key": r.key,
                "value_name": r.value_name,
                "bytes_base64": base64.b64encode(serialize([r])[8:]).decode("ascii"),
            }
            for r in to_registry_settings(policy)
        ],
    }


def build(out: Path, policy: FirewallPolicy) -> None:
    issues = policy.validate()
    if issues:
        raise ValueError("; ".join(i.code + ": " + i.message for i in issues))
    gpo = GPO(
        guid="31415926-5358-9793-2384-626433832796",
        name="StudioFwLane-candidate",
        domain="synthetic.test",
        settings=tuple(to_registry_settings(policy)),
    )
    issues = validate_gpo(gpo)
    if issues:
        raise ValueError("; ".join(i.code + ": " + i.message for i in issues))
    bundle = gpmc_backup_bundle(gpo)
    payloads = {
        ARCHIVE_NAME: bundle,
        "authoring.json": (json.dumps(authoring(policy), indent=2, sort_keys=True) + "\n").encode(),
        "expected.json": (
            json.dumps(expectation(policy, gpo), indent=2, sort_keys=True) + "\n"
        ).encode(),
    }
    out.mkdir(parents=True, exist_ok=True)
    for name, data in payloads.items():
        (out / name).write_bytes(data)
        print(f"{name} sha256={hashlib.sha256(data).hexdigest()}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    try:
        build(args.output_dir.resolve(), candidate_policy())
    except (ValueError, ValidationError) as exc:
        print(f"candidate refused: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
