#!/bin/bash
# smart_run.sh
# Automated Let's Encrypt sync and deployment script
# Now compares live certs with deployed certs and syncs if different,
# and restarts Docker containers only if s-ui memory exceeds threshold.

source $HOME/config.sh

# ==========================
# Logging
# ==========================
LOG_DIR="$(dirname "$LOG_FILE")"
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

    local src_hash dst_hash changed=false

    # Compare fullchain.pem
    src_hash=$(sudo sha256sum "$CERT_SRC/fullchain.pem" | awk '{print $1}')
    if [ -f "$CERT_DST/fullchain.pem" ]; then
        dst_hash=$(sha256sum "$CERT_DST/fullchain.pem" | awk '{print $1}')
    else
        dst_hash="MISSING"
    fi
    if [ "$src_hash" != "$dst_hash" ]; then
        log "Detected certificate update (fullchain.pem differs)"
        changed=true
    fi

    # Compare privkey.pem
    src_hash=$(sudo sha256sum "$CERT_SRC/privkey.pem" | awk '{print $1}')
    if [ -f "$CERT_DST/privkey.pem" ]; then
        dst_hash=$(sha256sum "$CERT_DST/privkey.pem" | awk '{print $1}')
    else
        dst_hash="MISSING"
    fi
    if [ "$src_hash" != "$dst_hash" ]; then
        log "Detected private key update (privkey.pem differs)"
        changed=true
    fi

    if [ "$changed" = true ]; then
        log "→ Certificates changed, syncing..."
        copy_cert
        # 不直接重启，重启由内存检查控制
    else
        log "Certificates are already up-to-date."
    fi
}

# ==========================
# Copy certificates
# ==========================
copy_cert() {
    log "Copying certificates to Nginx and Sing-box directories..."
    mkdir -p "$CERT_DST"
    sudo rsync -a --copy-links "$CERT_SRC"/ "$CERT_DST"/
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
# Check s-ui memory
# ==========================
check_memory() {
    local THRESHOLD_MB=200
    local mem_used

    mem_used=$(docker stats "$SINGBOX_CONTAINER" --no-stream --format "{{.MemUsage}}" \
        | awk '{print $1}' | sed 's/MiB//')
    mem_used=${mem_used%.*}

    if [ -z "$mem_used" ]; then
        log "Failed to get memory usage for container: $SINGBOX_CONTAINER"
        return
    fi

    log "Current $SINGBOX_CONTAINER memory usage: ${mem_used}MB"

    if [ "$mem_used" -gt "$THRESHOLD_MB" ]; then
        log "Memory exceeds threshold (${THRESHOLD_MB}MB), restarting containers..."
        reload_docker
    else
        log "Memory usage normal, no restart needed."
    fi
}

# ==========================
# Main logic
# ==========================
main() {
    local ACTIONS=("$@")

    if [ ${#ACTIONS[@]} -eq 0 ]; then
        log "No arguments provided. Running full check-sync-memory process."
        check_cert
        check_memory
        exit 0
    fi

    for action in "${ACTIONS[@]}"; do
        case "$action" in
            --check)  check_cert ;;
            --copy)   copy_cert ;;
            --reload) reload_docker ;;
            --mem)    check_memory ;;
            *) log "Unknown parameter: $action" ;;
        esac
    done
}

main "$@"