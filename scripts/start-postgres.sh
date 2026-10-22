#!/usr/bin/env bash
set -euo pipefail

compose=(docker compose -f docker-compose.postgres.yml)
case "${1:-up}" in
  up) "${compose[@]}" up -d --wait ;;
  down) "${compose[@]}" down ;;
  status) "${compose[@]}" ps ;;
  logs) "${compose[@]}" logs --follow postgres ;;
  *) echo "Usage: $0 {up|down|status|logs}" >&2; exit 2 ;;
esac
