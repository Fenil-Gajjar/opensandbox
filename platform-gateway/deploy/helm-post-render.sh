#!/bin/bash
# Helm post-renderer for OpenSandbox server chart.
#
# Fixes the wildcard/header mode conflict:
#   - Server config needs gateway.route.mode = "wildcard" (for browser-friendly URLs)
#   - Ingress binary needs --mode=header (only understands header/uri)
#   - The upstream chart uses one value for both → conflict
#
# This script receives rendered YAML on stdin, patches the ingress gateway
# Deployment to use --mode=header regardless of gatewayRouteMode, and outputs
# the modified YAML on stdout.
#
# Usage:
#   helm install opensandbox-server ./opensandbox-server \
#     --post-renderer ./helm-post-render.sh \
#     -f values.yaml
#
# Why post-renderer instead of modifying the chart:
#   - Zero changes to upstream OpenSandbox repo
#   - git pull always works cleanly
#   - helm upgrade works without manual steps

set -euo pipefail

# Read all rendered YAML from stdin, replace --mode=wildcard with --mode=header
# This only affects the ingress-gateway Deployment args (the only place --mode= appears)
sed 's/--mode=wildcard/--mode=header/g'
