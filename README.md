Arista eAPI Prometheus Exporter (for 7150S and similar)

Overview
This is a minimal Prometheus exporter that queries Arista EOS devices via eAPI (JSON-RPC) and exposes metrics consumable by Prometheus/Grafana. It has been tested with 7xx/71xx families; command outputs may vary slightly by EOS version.

Requirements
- Python 3.9+
- eAPI enabled on the switch (HTTPS recommended)

Switch eAPI quick setup (on EOS)
```
configure
management api http-commands
   protocol https
   no protocol http
   enable
   vrf default
!
username admin privilege 15 secret <password>
write memory
```

Install
```
pip install -r requirements.txt
```

Configure
Copy and edit the example config:
```
mkdir -p arista_exporter
cp arista_exporter/config.example.yaml arista_exporter/config.yaml
vim arista_exporter/config.yaml
```

Run
```
python -m arista_exporter.exporter -c arista_exporter/config.yaml
```

Metrics endpoint will be on http://0.0.0.0:9805/metrics by default.

Prometheus scrape example
```
scrape_configs:
  - job_name: 'arista'
    static_configs:
      - targets: ['exporter-host:9805']
```

Grafana
Import Prometheus as a data source and build panels using metrics such as:
- arista_device_info
- arista_interface_status
- arista_interface_octets
- arista_interface_errors_total
- arista_bgp_peer_state
- arista_bgp_prefixes

Docker (optional)
```
docker build -t arista-exporter .
docker run -p 9805:9805 -v $(pwd)/arista_exporter/config.yaml:/app/arista_exporter/config.yaml arista-exporter
```

Systemd (optional)
Create /etc/systemd/system/arista-exporter.service:
```
[Unit]
Description=Arista eAPI Prometheus Exporter
After=network-online.target

[Service]
User=arista
WorkingDirectory=/opt/arista-exporter
ExecStart=/usr/bin/python3 -m arista_exporter.exporter -c /opt/arista-exporter/arista_exporter/config.yaml
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Notes
- If your device uses self-signed certs, set ssl_verify: false in the target block.
- Adjust commands in the config if your EOS version differs.
- Use a dedicated low-privilege read-only account in production.

