#!/usr/bin/env bash
# Tab-completion for the `pandamonium` umbrella + every `pandamonium-*` CLI.
#
# Source from your shell rc:
#     source /path/to/pandamonium-ui/scripts/_completion/pandamonium.bash
#
# Or wire it once per machine:
#     sudo install -m 644 pandamonium.bash /etc/bash_completion.d/pandamonium
#
# What it does:
#   - On the first word after `pandamonium`, complete with the list of
#     subcommands (`mail`, `calendar`, ...).
#   - On subsequent words, complete with the subcommand's first-token
#     subcommands (`list`, `show`, ...) which we cache by parsing the
#     tool's own --help output. Updates lazily; refresh by running
#     `_pandamonium_refresh_cache`.
#   - Same completion works for the individual `pandamonium-foo` scripts.

_pandamonium_scripts_dir() {
    # Resolve the scripts/ dir from the script that sources us. We assume
    # the user sourced the file directly out of scripts/_completion/.
    local self="${BASH_SOURCE[0]}"
    while [ -L "$self" ]; do self=$(readlink "$self"); done
    cd "$(dirname "$self")/.." && pwd
}

declare -A _PANDAMONIUM_SUBS_CACHE=()

_pandamonium_refresh_cache() {
    local dir="$(_pandamonium_scripts_dir)"
    _PANDAMONIUM_SUBS_CACHE=()
    # Prefer the project venv's Python so deps (bcrypt, sqlalchemy, ...)
    # resolve. Falls back to system `python3` for container installs.
    local py="$dir/../venv/bin/python"
    [ -x "$py" ] || py="$(command -v python3)"
    local f
    for f in "$dir"/pandamonium-*; do
        [ -x "$f" ] || continue
        case "$f" in *.bak|*.pyc|*.pre-*) continue ;; esac
        local name="$(basename "$f")"
        local sub="${name#pandamonium-}"
        local help_out
        help_out=$("$py" "$f" --help 2>/dev/null) || continue
        local commands
        commands=$(echo "$help_out" | grep -oE '\{[a-z0-9_,-]+\}' | head -1 \
            | tr -d '{}' | tr ',' ' ')
        _PANDAMONIUM_SUBS_CACHE[$sub]="$commands"
    done
}

_pandamonium_complete() {
    [ ${#_PANDAMONIUM_SUBS_CACHE[@]} -eq 0 ] && _pandamonium_refresh_cache

    local cur="${COMP_WORDS[COMP_CWORD]}"
    local cmd="${COMP_WORDS[0]}"

    # `pandamonium <tab>` → list every subcommand
    if [ "$cmd" = "pandamonium" ]; then
        if [ "$COMP_CWORD" -eq 1 ]; then
            local subs="${!_PANDAMONIUM_SUBS_CACHE[@]} help"
            COMPREPLY=($(compgen -W "$subs" -- "$cur"))
            return 0
        fi
        # `pandamonium foo <tab>` — complete with foo's own subcommands
        local sub="${COMP_WORDS[1]}"
        # `pandamonium help <tab>` lists every subcommand
        if [ "$sub" = "help" ] && [ "$COMP_CWORD" -eq 2 ]; then
            COMPREPLY=($(compgen -W "${!_PANDAMONIUM_SUBS_CACHE[*]}" -- "$cur"))
            return 0
        fi
        if [ "$COMP_CWORD" -eq 2 ]; then
            COMPREPLY=($(compgen -W "${_PANDAMONIUM_SUBS_CACHE[$sub]}" -- "$cur"))
            return 0
        fi
        return 0
    fi

    # Direct `pandamonium-foo <tab>` (no umbrella)
    local sub="${cmd#pandamonium-}"
    if [ "$COMP_CWORD" -eq 1 ]; then
        COMPREPLY=($(compgen -W "${_PANDAMONIUM_SUBS_CACHE[$sub]}" -- "$cur"))
        return 0
    fi
}

# Register the completion for every pandamonium-* script + the umbrella.
complete -F _pandamonium_complete pandamonium
for f in "$(_pandamonium_scripts_dir)"/pandamonium-*; do
    [ -x "$f" ] || continue
    case "$f" in *.bak|*.pyc|*.pre-*) continue ;; esac
    complete -F _pandamonium_complete "$(basename "$f")"
done
