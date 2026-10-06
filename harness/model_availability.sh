#!/usr/bin/env bash
# Shared model-availability guard, sourced by harness entry points that launch an Ollama-backed
# agent. The operator chooses the provider and the model (Ollama, Hugging Face, Vast, an API);
# the factory never picks one and never downloads or installs one. Codex's `--oss` mode and
# `ollama launch` both pull a missing model on first use, so a launch that names no model, or a
# model this machine does not already have, is refused here before either can start a download.

# factory_require_ollama_model <caller> <env-var-name> <model>
factory_require_ollama_model() {
  local caller="$1" name="$2" model="$3"
  if [ -z "$model" ]; then
    echo "$caller: set $name to an Ollama model this machine already has; the factory never picks or downloads a model" >&2
    return 64
  fi
  case "$model" in
    *[!A-Za-z0-9._:/-]*)
      echo "$caller: $name is not a valid Ollama model name: $model" >&2
      return 64 ;;
  esac
  if ! command -v ollama >/dev/null 2>&1; then
    echo "$caller: ollama is not installed; the factory does not install providers. Install it yourself or use another agent" >&2
    return 69
  fi
  if ! ollama show "$model" >/dev/null 2>&1; then
    echo "$caller: Ollama model '$model' is not available here (or the Ollama server is not running). The factory never downloads models: make it available yourself (for example \`ollama pull $model\`) or set $name to one you have" >&2
    return 69
  fi
}
