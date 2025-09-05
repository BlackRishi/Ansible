#!/usr/bin/env bash

# service_control.sh - Control a systemd service across multiple remote nodes.
#
# Usage examples:
#   ./service_control.sh -a start   -s nginx -n host1,host2
#   ./service_control.sh -a restart -s httpd -f hosts.txt
#   ./service_control.sh -a status  -s docker -n host1 --parallel
#
# Options:
#   -a, --action    Required. One of: start | stop | restart | status
#   -s, --service   Required. The systemd service name to operate on.
#   -n, --nodes     Comma-separated list of nodes (e.g. "host1,host2").
#   -f, --file      File containing one node per line (ignored if -n is used).
#   -u, --user      SSH user (default: current user).
#       --parallel  Run operations in parallel (background jobs).
#   -h, --help      Show this help.
#
# Notes:
#   • Requires SSH access without interactive password prompts (e.g. SSH keys).
#   • Outputs a summary of successes/failures at the end.

set -euo pipefail

print_help() {
  grep -E '^#' "$0" | cut -c 3-
}

ACTION=""
SERVICE=""
NODE_LIST=""
NODE_FILE=""
SSH_USER="${USER:-$(whoami)}"
PARALLEL=false

# Parse arguments
while [[ $# -gt 0 ]]; do
  case "$1" in
    -a|--action)
      ACTION="$2"; shift 2;;
    -s|--service)
      SERVICE="$2"; shift 2;;
    -n|--nodes)
      NODE_LIST="$2"; shift 2;;
    -f|--file)
      NODE_FILE="$2"; shift 2;;
    -u|--user)
      SSH_USER="$2"; shift 2;;
    --parallel)
      PARALLEL=true; shift;;
    -h|--help)
      print_help; exit 0;;
    *)
      echo "Unknown option: $1" >&2; print_help; exit 1;;
  esac
done

if [[ -z "$ACTION" || -z "$SERVICE" ]]; then
  echo "Error: --action and --service are required." >&2
  print_help; exit 1
fi

if [[ "$ACTION" != "start" && "$ACTION" != "stop" && "$ACTION" != "restart" && "$ACTION" != "status" ]]; then
  echo "Error: Invalid action '$ACTION'." >&2; exit 1
fi

# Build nodes array
nodes=()
if [[ -n "$NODE_LIST" ]]; then
  IFS=',' read -r -a nodes <<< "$NODE_LIST"
elif [[ -n "$NODE_FILE" ]]; then
  if [[ ! -f "$NODE_FILE" ]]; then
    echo "Error: file '$NODE_FILE' not found" >&2; exit 1
  fi
  mapfile -t nodes < "$NODE_FILE"
else
  echo "Error: You must specify --nodes or --file." >&2
  print_help; exit 1
fi

if [[ ${#nodes[@]} -eq 0 ]]; then
  echo "Error: No nodes specified." >&2; exit 1
fi

# Track results
declare -A RESULTS

run_on_node() {
  local node="$1"
  local cmd="sudo systemctl $ACTION $SERVICE"
  if [[ "$ACTION" == "status" ]]; then
    cmd="systemctl status --no-pager --full $SERVICE"
  fi

  if ssh -o BatchMode=yes -o ConnectTimeout=5 "${SSH_USER}@${node}" "$cmd"; then
    RESULTS["$node"]="SUCCESS"
  else
    RESULTS["$node"]="FAILED"
  fi
}

jobs=()
for node in "${nodes[@]}"; do
  echo "==== $node ===="
  if $PARALLEL; then
    run_on_node "$node" &
    jobs+=("$!")
  else
    run_on_node "$node"
  fi
  echo
done

# Wait for background jobs
if $PARALLEL && ((${#jobs[@]})); then
  wait "${jobs[@]}"
fi

echo "Summary:"
for node in "${nodes[@]}"; do
  status="${RESULTS[$node]:-UNKNOWN}"
  printf "  %-20s %s\n" "$node" "$status"
  [[ "$status" == "FAILED" ]] && overall_failed=true
done

if [[ "${overall_failed:-}" == true ]]; then
  exit 1
fi

exit 0