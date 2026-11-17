#!/bin/sh
# Backup/restore of the Postgres database, executed inside the official postgres:16 image so
# pg_dump/pg_restore always match the server major version.
#
#   backup.sh backup   -> custom-format dump into $BACKUP_DIR and prune to $BACKUP_KEEP
#   backup.sh list     -> newest first
#   backup.sh restore FILE --yes
#   backup.sh loop     -> backup, then sleep $BACKUP_INTERVAL_SECONDS, forever
set -eu

DIR="${BACKUP_DIR:-/backups}"
KEEP="${BACKUP_KEEP:-7}"
INTERVAL="${BACKUP_INTERVAL_SECONDS:-86400}"
PREFIX="imovel-"
SUFFIX=".dump"

mkdir -p "$DIR"

do_backup() {
    # busybox date has no sub-second precision, so make the name unique explicitly:
    # the safety dump must never overwrite the file being restored.
    base="$DIR/${PREFIX}$(date -u +%Y%m%d-%H%M%S)"
    target="${base}${SUFFIX}"
    n=1
    while [ -e "$target" ]; do
        target="${base}-${n}${SUFFIX}"
        n=$((n + 1))
    done
    pg_dump --format=custom --no-owner --file "$target"
    echo "backup written to $target ($(du -h "$target" | cut -f1))"
    prune
}

prune() {
    keep="$KEEP"
    [ "$keep" -gt 0 ] 2>/dev/null || return 0
    ls -1t "$DIR"/${PREFIX}*${SUFFIX} 2>/dev/null | tail -n +$((keep + 1)) | while read -r old; do
        rm -f "$old" && echo "pruned $old"
    done
}

case "${1:-backup}" in
    backup)
        do_backup
        ;;
    list)
        ls -1t "$DIR"/${PREFIX}*${SUFFIX} 2>/dev/null | while read -r f; do
            printf '%s  %8s  %s\n' "$(date -r "$f" '+%d/%m/%Y %H:%M')" "$(du -h "$f" | cut -f1)" "$f"
        done
        ;;
    restore)
        file="${2:-}"
        confirm="${3:-}"
        if [ -z "$file" ]; then
            echo "usage: backup.sh restore FILE --yes" >&2
            exit 2
        fi
        if [ "$confirm" != "--yes" ]; then
            echo "restore replaces the live database; pass --yes to confirm" >&2
            exit 2
        fi
        [ -f "$file" ] || { echo "backup not found: $file" >&2; exit 2; }
        echo "safety backup before restore:"
        do_backup
        pg_restore --clean --if-exists --no-owner --single-transaction --dbname "$PGDATABASE" "$file"
        echo "restored $file"
        ;;
    loop)
        while :; do
            do_backup || echo "backup failed" >&2
            sleep "$INTERVAL"
        done
        ;;
    *)
        echo "unknown command: $1" >&2
        exit 2
        ;;
esac
