#!/usr/bin/env bash
#
# devbox-setup.sh — apply the known cluster-side Flyte devbox workarounds to a
# fresh `flyte-devbox` container so `python -m app.admin_app` deploys cleanly.
#
# These patches live in the k3s addon manifest (and CoreDNS) and are LOST when
# the devbox container is recreated, so re-run this after every fresh devbox.
# The script is idempotent — re-running an already-patched box is a no-op for
# the edits (the restarts still fire).
#
# Source of truth + rationale for each step:
#   .opencode/reference/devbox_workarounds.md
#
# What this DOES automate (cluster-side, lost on container recreate):
#   1. Storage signed-URL endpoint   localhost:30002 → rustfs-svc.flyte:9000
#   2. Serving domain off `.localhost` → ${DEVBOX_DOMAIN} (Knative + Flyte)
#   3. CoreDNS wildcard  *.${DEVBOX_DOMAIN} → node InternalIP  (so App.endpoint
#      resolves in-cluster on the :30081 NodePort)
#   4. flyte-binary + coredns restarts, racing the addon controller correctly
#
# internalApps FLYTE_AWS_ENDPOINT (`rustfs.flyte` → `rustfs-svc.flyte`) was
# fixed upstream — the devbox image now ships the correct service name, so
# there is nothing to patch. The verify step still warns if it ever regresses.
#
# What it does NOT do (already handled in app code — see the workarounds doc):
#   - AppEnvironment secret baking into env_vars (app/admin_app.py)
#   - init_in_cluster / SDK-over-CLI provisioning (app/init.py, app/provision.py)
#   - App.endpoint vs App.url, Secure-cookie toggle, code-bundle include=/naming
#
# Laptop-side DNS (sudo, macOS) is PRINTED by default; run with --laptop to apply.
#
# Usage:
#   cli/devbox-setup.sh [--dry-run] [--laptop] [--verify-pod] [--domain D]
#
set -euo pipefail

CONTAINER="${DEVBOX_CONTAINER:-flyte-devbox}"
DOMAIN="${DEVBOX_DOMAIN:-devbox.stargazer.bio}"
MANIFEST="/var/lib/rancher/k3s/server/manifests/flyte.yaml"
DRY_RUN=0
DO_LAPTOP=0
VERIFY_POD=0

c_blue=$'\033[1;34m'; c_green=$'\033[1;32m'; c_yellow=$'\033[1;33m'; c_red=$'\033[1;31m'; c_off=$'\033[0m'
log()  { printf '%s▸ %s%s\n' "$c_blue"  "$*" "$c_off"; }
ok()   { printf '%s✓ %s%s\n' "$c_green" "$*" "$c_off"; }
warn() { printf '%s! %s%s\n' "$c_yellow" "$*" "$c_off" >&2; }
die()  { printf '%s✗ %s%s\n' "$c_red"  "$*" "$c_off" >&2; exit 1; }

# Print the leading comment block (stops at the first non-comment line, so it
# survives edits to the header without a hardcoded line count).
usage() { sed -n '2,${/^#/!q;p;}' "$0" | sed 's/^# \{0,1\}//'; exit 0; }

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run)    DRY_RUN=1 ;;
        --laptop)     DO_LAPTOP=1 ;;
        --verify-pod) VERIFY_POD=1 ;;
        --domain)     DOMAIN="${2:?--domain needs a value}"; shift ;;
        -h|--help)    usage ;;
        *) die "unknown argument: $1 (try --help)" ;;
    esac
    shift
done

# Escaped form for the CoreDNS regex (dots literal).
DOMAIN_RE="${DOMAIN//./\\.}"

dex()  { docker exec "$CONTAINER" "$@"; }
kc()   { docker exec "$CONTAINER" kubectl "$@"; }
# Run a mutating command, or just print it under --dry-run.
run()  { if [ "$DRY_RUN" = 1 ]; then printf '   %s[dry-run]%s %s\n' "$c_yellow" "$c_off" "$*"; else "$@"; fi; }

# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------
command -v docker >/dev/null 2>&1 || die "docker not found on PATH"
docker ps --format '{{.Names}}' | grep -qx "$CONTAINER" \
    || die "devbox container '$CONTAINER' is not running (set DEVBOX_CONTAINER to override)"
dex test -f "$MANIFEST" || die "addon manifest not found at $MANIFEST inside $CONTAINER"
log "Target container=$CONTAINER  domain=$DOMAIN  dry-run=$DRY_RUN"

# ---------------------------------------------------------------------------
# 1–2. Patch the k3s addon manifest (the ConfigMaps are addon-owned; editing
#      them directly reverts, so we patch the source manifest and let the
#      controller re-render).
# ---------------------------------------------------------------------------
log "Patching addon manifest ($MANIFEST)"
run dex sed -i \
    -e 's|endpoint: http://localhost:30002|endpoint: http://rustfs-svc.flyte:9000|g' \
    -e 's|^  localhost: ""|  '"$DOMAIN"': ""|' \
    -e "s|baseDomain: localhost|baseDomain: $DOMAIN|" \
    "$MANIFEST"
ok "Manifest patched (signed-URL endpoint, Knative domain, baseDomain)"

# ---------------------------------------------------------------------------
# Wait for the addon controller to re-render the LIVE ConfigMaps. flyte-binary
# reads config once at startup, so restarting before the ConfigMap is patched
# boots stale config — the deploy then fails identically and looks unpatched.
# ---------------------------------------------------------------------------
log "Waiting for the k3s addon controller to re-render ConfigMaps"
if [ "$DRY_RUN" = 1 ]; then
    printf '   %s[dry-run]%s poll flyte-binary-config / config-domain until patched\n' "$c_yellow" "$c_off"
else
    rendered=0
    for _ in $(seq 1 30); do
        cm="$(kc get cm flyte-binary-config -n flyte -o yaml 2>/dev/null || true)"
        dom="$(kc get cm config-domain -n knative-serving -o jsonpath='{.data}' 2>/dev/null || true)"
        if grep -q "baseDomain: $DOMAIN" <<<"$cm" \
            && grep -q "$DOMAIN" <<<"$dom"; then
            rendered=1; break
        fi
        sleep 2
    done
    [ "$rendered" = 1 ] && ok "Live ConfigMaps reflect the patch" \
        || warn "ConfigMaps not fully patched after 60s — inspect manually before relying on a deploy"
fi

# ---------------------------------------------------------------------------
# Restart flyte-binary so it loads the patched config.
# ---------------------------------------------------------------------------
log "Restarting flyte-binary"
run kc rollout restart deployment/flyte-binary -n flyte
# Non-fatal: a slow rollout shouldn't abort the script before later steps run.
# (flyte-binary needs healthy cluster DNS to start, so a stall here usually
# means coredns is unhealthy — the verify section reports the true state.)
run kc rollout status deployment/flyte-binary -n flyte --timeout=180s \
    || warn "flyte-binary rollout slow/incomplete — continuing; check 'kubectl get pods -n flyte'"
ok "flyte-binary restart issued"

# ---------------------------------------------------------------------------
# 4. CoreDNS wildcard: *.${DOMAIN} → node InternalIP. App.endpoint carries the
#    :30081 NodePort, only reachable on a node IP (not a ClusterIP). The AAAA
#    template returns NOERROR-empty so glibc falls back to the A record.
# ---------------------------------------------------------------------------
log "Applying coredns-custom for *.$DOMAIN"
if [ "$DRY_RUN" = 1 ]; then
    printf '   %s[dry-run]%s kubectl apply coredns-custom (node IP fetched at apply time)\n' "$c_yellow" "$c_off"
else
    NODEIP="$(kc get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}')"
    [ -n "$NODEIP" ] || die "could not determine node InternalIP"
    docker exec -i "$CONTAINER" kubectl apply -f - <<YAML
apiVersion: v1
kind: ConfigMap
metadata:
  name: coredns-custom
  namespace: kube-system
data:
  devbox-domain.server: |
    ${DOMAIN}:53 {
        template IN A {
            match .*\\.${DOMAIN_RE}\\.\$
            answer "{{ .Name }} 60 IN A ${NODEIP}"
        }
        template IN AAAA {
            match .*\\.${DOMAIN_RE}\\.\$
            rcode NOERROR
        }
    }
YAML
    ok "coredns-custom applied (*.$DOMAIN → $NODEIP)"
fi
log "Restarting coredns"
run kc rollout restart deployment/coredns -n kube-system
run kc rollout status deployment/coredns -n kube-system --timeout=120s \
    || warn "coredns rollout slow/incomplete — a CrashLoop here means the custom config is invalid; check 'kubectl logs -n kube-system -l k8s-app=kube-dns'"
ok "coredns restart issued"

# ---------------------------------------------------------------------------
# Verify (read-only)
# ---------------------------------------------------------------------------
if [ "$DRY_RUN" = 0 ]; then
    log "Verifying cluster-side state"
    cm="$(kc get cm flyte-binary-config -n flyte -o yaml 2>/dev/null || true)"
    # Regression guard: the bare `rustfs.flyte` service name was an upstream
    # devbox bug, fixed in the image — we no longer patch it, only notice it.
    grep -qE 'rustfs\.flyte:9000' <<<"$cm" \
        && warn "flyte-binary-config has a bare rustfs.flyte:9000 — upstream regression, patch it by hand (see devbox_workarounds.md)" \
        || ok "no bare rustfs.flyte endpoints remain"
    grep -q "baseDomain: $DOMAIN" <<<"$cm" && ok "baseDomain=$DOMAIN" || warn "baseDomain not set to $DOMAIN"
    kc get cm coredns-custom -n kube-system -o name >/dev/null 2>&1 \
        && ok "coredns-custom present" || warn "coredns-custom missing"

    if [ "$VERIFY_POD" = 1 ]; then
        log "Throwaway-pod DNS/HTTP check (may take ~30s)"
        kc run sg-devbox-check --rm -i --restart=Never \
            --image=localhost:30000/notebook-app:latest --command -- \
            python -c "import socket; print('resolves:', socket.getaddrinfo('admin-app-flytesnacks-development.$DOMAIN', 30081)[0][4])" \
            2>/dev/null || warn "pod check failed (image may not be built yet — non-fatal)"
    fi
fi

# ---------------------------------------------------------------------------
# Laptop-side DNS (macOS, sudo). Printed by default; applied with --laptop.
# ---------------------------------------------------------------------------
laptop_steps() {
    cat <<EOF
  # 1) Resolve the storage host to the auto port-forward (app/admin_app.py opens :9000):
  echo '127.0.0.1 rustfs-svc.flyte' | sudo tee -a /etc/hosts

  # 2) Wildcard-resolve *.$DOMAIN → 127.0.0.1 (the published :30081 port is unchanged):
  brew install dnsmasq
  echo 'address=/$DOMAIN/127.0.0.1' >> "\$(brew --prefix)/etc/dnsmasq.conf"
  sudo brew services restart dnsmasq
  sudo mkdir -p /etc/resolver
  printf 'nameserver 127.0.0.1\n' | sudo tee /etc/resolver/$DOMAIN
  # verify: scutil --dns | grep -A1 $DOMAIN ; ping -c1 anything.$DOMAIN  # → 127.0.0.1
EOF
}

if [ "$DO_LAPTOP" = 1 ]; then
    log "Applying laptop-side DNS (needs sudo + Homebrew)"
    command -v brew >/dev/null 2>&1 || die "Homebrew required for --laptop"
    grep -q 'rustfs-svc.flyte' /etc/hosts 2>/dev/null \
        || run bash -c "echo '127.0.0.1 rustfs-svc.flyte' | sudo tee -a /etc/hosts >/dev/null"
    brew list dnsmasq >/dev/null 2>&1 || run brew install dnsmasq
    conf="$(brew --prefix)/etc/dnsmasq.conf"
    grep -q "address=/$DOMAIN/127.0.0.1" "$conf" 2>/dev/null \
        || run bash -c "echo 'address=/$DOMAIN/127.0.0.1' >> '$conf'"
    run sudo brew services restart dnsmasq
    run sudo mkdir -p /etc/resolver
    run bash -c "printf 'nameserver 127.0.0.1\n' | sudo tee /etc/resolver/$DOMAIN >/dev/null"
    ok "Laptop-side DNS applied"
else
    log "Laptop-side DNS (run once; not automated — re-run with --laptop to apply):"
    laptop_steps
fi

ok "Devbox cluster-side workarounds applied. Deploy with: uv run python -m app.admin_app"
