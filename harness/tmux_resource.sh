#!/usr/bin/env bash
# A successful format expansion does not prove a named session exists: tmux can
# print empty fields and exit zero for a missing target. Session identities are exact.
factory_tmux_resource_exists() {
  case "$1" in
    tmux-session) tmux has-session -t "=$2" ;;
    tmux-window) tmux display-message -p -t "$2" '#{session_name}:#{window_name}' ;;
    *) return 64 ;;
  esac
}
