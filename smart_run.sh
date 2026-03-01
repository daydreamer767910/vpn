#!/bin/bash
# smart_run.sh
# Automated Let's Encrypt sync and deployment script
# No longer renews certificates (systemd handles that)
# Now compares live certs with deployed certs and syncs if different.
source $HOME/config.sh

# ==========================
# Logging
# ==========================
LOG_DIR="$(dirname "$LOG_FILE")"

# 确保日志目录存在
mkdir -p "$LOG_DIR"
exec >> "$LOG_FILE" 2>&1
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

# ==========================
# Compare certificate files (by hash)
# ==========================
check_cert() {
    log "Checking if deployed certificates match latest system certificates..."

    local src_hash
    local dst_hash
    local changed=false

    # Compare fullchain.pem
    src_hash=$(sudo sha256sum "$CERT_SRC/fullchain.pem" | awk '{print $1}')
    if [ -f "$NGINX_CERT_DST/fullchain.pem" ]; then
        dst_hash=$(sha256sum "$NGINX_CERT_DST/fullchain.pem" | awk '{print $1}')
    else
        dst_hash="MISSING"
    fi

    if [ "$src_hash" != "$dst_hash" ]; then
        log "Detected certificate update (fullchain.pem differs)"
        changed=true
    fi

    # Compare privkey.pem
    src_hash=$(sudo sha256sum "$CERT_SRC/privkey.pem" | awk '{print $1}')
    if [ -f "$NGINX_CERT_DST/privkey.pem" ]; then
        dst_hash=$(sha256sum "$NGINX_CERT_DST/privkey.pem" | awk '{print $1}')
    else
        dst_hash="MISSING"
    fi

    if [ "$src_hash" != "$dst_hash" ]; then
        log "Detected private key update (privkey.pem differs)"
        changed=true
    fi

    if [ "$changed" = true ]; then
        log "→ Certificates changed, syncing and reloading services..."
        copy_cert
        reload_docker
    else
        log "Certificates are already up-to-date. No action needed."
    fi
}

# ==========================
# Copy certificates and fix permissions
# ==========================
copy_cert() {
    log "Copying certificates to Nginx and Sing-box directories..."
    mkdir -p "$NGINX_CERT_DST" "$SINGBOX_CERT_DST"
    sudo rsync -a --copy-links "$CERT_SRC"/ "$NGINX_CERT_DST"/
    sudo rsync -a --copy-links "$CERT_SRC"/ "$SINGBOX_CERT_DST"/
    log "Certificates copied successfully."
}

# ==========================
# Restart Docker containers
# ==========================
reload_docker() {
    log "Restarting Docker containers: $NGINX_CONTAINER, $SINGBOX_CONTAINER"
    sudo docker restart "$NGINX_CONTAINER" "$SINGBOX_CONTAINER"
    if [ $? -eq 0 ]; then
        log "Docker containers restarted successfully"
    else
        log "Docker restart failed"
    fi
}

# ==========================
# Main logic
# ==========================
main() {
    local ACTIONS=("$@")

    if [ ${#ACTIONS[@]} -eq 0 ]; then
        log "No arguments provided. Running full check-sync process."
        check_cert
        exit 0
    fi

    for action in "${ACTIONS[@]}"; do
        case "$action" in
            --check)  check_cert ;;
            --copy)   copy_cert ;;
            --reload) reload_docker ;;
            *) log "Unknown parameter: $action" ;;
        esac
    done
}

main "$@"

