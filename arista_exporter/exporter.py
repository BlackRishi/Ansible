#!/usr/bin/env python3
import argparse
import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import requests
import yaml
from prometheus_client import CollectorRegistry, Gauge, Counter, generate_latest, CONTENT_TYPE_LATEST
from prometheus_client import start_http_server
from http.server import BaseHTTPRequestHandler, HTTPServer


logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')


@dataclass
class Target:
    name: str
    host: str
    port: int
    username: str
    password: str
    ssl_verify: bool = False

    def url(self) -> str:
        return f"https://{self.host}:{self.port}/command-api"


class AristaEapiClient:
    def __init__(self, target: Target, session: Optional[requests.Session] = None) -> None:
        self.target = target
        self.session = session or requests.Session()
        self.session.verify = target.ssl_verify
        self.session.headers.update({
            'Content-Type': 'application/json'
        })

    def run_commands(self, commands: List[str], encoding: str = 'json') -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
        payload = {
            'jsonrpc': '2.0',
            'method': 'runCmds',
            'params': {
                'version': 1,
                'cmds': commands,
                'format': encoding
            },
            'id': 1
        }
        try:
            response = self.session.post(
                self.target.url(),
                data=json.dumps(payload),
                auth=(self.target.username, self.target.password),
                timeout=10
            )
            response.raise_for_status()
            data = response.json()
            if 'error' in data:
                return None, str(data['error'])
            return data.get('result', []), None
        except Exception as exc:
            return None, str(exc)


class Exporter:
    def __init__(self, config: Dict[str, Any]) -> None:
        self.targets: List[Target] = [Target(**t) for t in config.get('targets', [])]
        self.commands: List[str] = config.get('commands', [
            'show version',
            'show interfaces status',
            'show interfaces counters',
        ])
        self.scrape_interval: int = int(config.get('scrape_interval', 15))
        self.listen_address: str = config.get('listen_address', '0.0.0.0')
        self.listen_port: int = int(config.get('listen_port', 9805))

        self.registry = CollectorRegistry()
        # Generic exporter metrics
        self.scrape_failures = Counter(
            'arista_exporter_scrape_failures_total',
            'Total scrape failures per target',
            ['target'],
            registry=self.registry
        )
        self.last_scrape_timestamp = Gauge(
            'arista_exporter_last_scrape_timestamp_seconds',
            'Unix time of last successful scrape per target',
            ['target'],
            registry=self.registry
        )

        # Example metrics from common outputs
        self.device_info = Gauge(
            'arista_device_info',
            'Static device metadata',
            ['target', 'model', 'serial_number', 'version', 'hostname'],
            registry=self.registry
        )

        self.interface_status = Gauge(
            'arista_interface_status',
            'Interface status: 1=up,0=down',
            ['target', 'interface', 'description', 'vlan'],
            registry=self.registry
        )

        self.interface_counters = Gauge(
            'arista_interface_octets',
            'Interface octet counters',
            ['target', 'interface', 'direction'],
            registry=self.registry
        )

        self.interface_errors = Gauge(
            'arista_interface_errors_total',
            'Interface error counters',
            ['target', 'interface', 'type'],
            registry=self.registry
        )

        self.bgp_peers = Gauge(
            'arista_bgp_peer_state',
            'BGP peer state: 1=Established else 0',
            ['target', 'peer_ip', 'asn'],
            registry=self.registry
        )

        self.bgp_prefixes = Gauge(
            'arista_bgp_prefixes',
            'BGP prefixes per peer',
            ['target', 'peer_ip', 'type'],
            registry=self.registry
        )

        self._stop_event = threading.Event()

    def start(self) -> None:
        thread = threading.Thread(target=self._scrape_loop, daemon=True)
        thread.start()

        # Start HTTP server for metrics
        class MetricsHandler(BaseHTTPRequestHandler):
            def do_GET(self_inner):
                if self_inner.path == '/metrics':
                    output = generate_latest(self.registry)
                    self_inner.send_response(200)
                    self_inner.send_header('Content-Type', CONTENT_TYPE_LATEST)
                    self_inner.send_header('Content-Length', str(len(output)))
                    self_inner.end_headers()
                    self_inner.wfile.write(output)
                else:
                    self_inner.send_response(404)
                    self_inner.end_headers()

        httpd = HTTPServer((self.listen_address, self.listen_port), MetricsHandler)
        logging.info("Exporter listening on %s:%s", self.listen_address, self.listen_port)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            self._stop_event.set()
            httpd.shutdown()

    def _scrape_loop(self) -> None:
        while not self._stop_event.is_set():
            start_time = time.time()
            for target in self.targets:
                self._scrape_target(target)
            elapsed = time.time() - start_time
            sleep_for = max(0, self.scrape_interval - elapsed)
            self._stop_event.wait(timeout=sleep_for)

    def _scrape_target(self, target: Target) -> None:
        client = AristaEapiClient(target)
        result, error = client.run_commands(self.commands, encoding='json')
        if error:
            logging.warning("Scrape failed for %s: %s", target.name, error)
            self.scrape_failures.labels(target=target.name).inc()
            return

        # Map command results to parsers by index
        parsers = [
            self._parse_show_version,
            self._parse_show_interfaces_status,
            self._parse_show_interfaces_counters,
            self._parse_show_ip_bgp_summary,
            self._parse_show_lldp_neighbors_detail,
        ]
        for idx, cmd_result in enumerate(result):
            if idx < len(parsers) and cmd_result is not None:
                try:
                    parsers[idx](target, cmd_result)
                except Exception as exc:
                    logging.debug("Parser %d failed: %s", idx, exc)

        self.last_scrape_timestamp.labels(target=target.name).set(time.time())

    # Parsers
    def _parse_show_version(self, target: Target, data: Dict[str, Any]) -> None:
        model = data.get('modelName', '')
        serial = data.get('serialNumber', '')
        version = data.get('version', '')
        hostname = data.get('hostname', target.name)
        self.device_info.labels(target=target.name, model=model, serial_number=serial, version=version, hostname=hostname).set(1)

    def _parse_show_interfaces_status(self, target: Target, data: Dict[str, Any]) -> None:
        interfaces = data.get('interfaceStatuses') or data.get('interfaces') or {}
        for ifname, ifdata in interfaces.items():
            link_status = ifdata.get('linkStatus') or ifdata.get('linkStatus', '')
            admin_state = ifdata.get('lineProtocolStatus') or ifdata.get('adminState', '')
            is_up = 1 if str(link_status).lower() == 'up' or str(admin_state).lower() == 'up' else 0
            description = ifdata.get('description') or ''
            vlan = str(ifdata.get('vlan', ''))
            self.interface_status.labels(target=target.name, interface=ifname, description=description, vlan=vlan).set(is_up)

    def _parse_show_interfaces_counters(self, target: Target, data: Dict[str, Any]) -> None:
        interfaces = data.get('interfaces') or {}
        for ifname, counters in interfaces.items():
            in_octets = counters.get('inOctets') or counters.get('inOctetsCounter') or 0
            out_octets = counters.get('outOctets') or counters.get('outOctetsCounter') or 0
            input_errors = counters.get('inputErrors') or 0
            output_errors = counters.get('outputErrors') or 0
            self.interface_counters.labels(target=target.name, interface=ifname, direction='in').set(float(in_octets))
            self.interface_counters.labels(target=target.name, interface=ifname, direction='out').set(float(out_octets))
            self.interface_errors.labels(target=target.name, interface=ifname, type='input').set(float(input_errors))
            self.interface_errors.labels(target=target.name, interface=ifname, type='output').set(float(output_errors))

    def _parse_show_ip_bgp_summary(self, target: Target, data: Dict[str, Any]) -> None:
        peers = data.get('vrfs', {}).get('default', {}).get('peers', {})
        for peer_ip, peer in peers.items():
            state = peer.get('peerState', '')
            asn = str(peer.get('asn', ''))
            established = 1 if str(state).lower() == 'established' else 0
            self.bgp_peers.labels(target=target.name, peer_ip=peer_ip, asn=asn).set(established)
            prefixes = peer.get('prefixReceived', 0)
            self.bgp_prefixes.labels(target=target.name, peer_ip=peer_ip, type='received').set(float(prefixes))

    def _parse_show_lldp_neighbors_detail(self, target: Target, data: Dict[str, Any]) -> None:
        # Example placeholder; you can add LLDP gauges if needed later
        return


def load_config(path: str) -> Dict[str, Any]:
    with open(path, 'r') as f:
        return yaml.safe_load(f) or {}


def main() -> None:
    parser = argparse.ArgumentParser(description='Arista eAPI Prometheus Exporter')
    parser.add_argument('-c', '--config', default='arista_exporter/config.yaml', help='Path to YAML config')
    args = parser.parse_args()

    config = load_config(args.config)
    exporter = Exporter(config)
    exporter.start()


if __name__ == '__main__':
    main()

