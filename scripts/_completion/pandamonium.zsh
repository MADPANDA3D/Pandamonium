#compdef pandamonium pandamonium-backup pandamonium-calendar pandamonium-contacts pandamonium-cookbook pandamonium-docs pandamonium-gallery pandamonium-mail pandamonium-mcp pandamonium-memory pandamonium-notes pandamonium-personal pandamonium-preset pandamonium-research pandamonium-sessions pandamonium-signature pandamonium-skills pandamonium-tasks pandamonium-theme pandamonium-webhook
# Zsh tab-completion for the pandamonium umbrella + sub-CLIs.
#
# Drop in any directory on $fpath, e.g.:
#     fpath=(/path/to/pandamonium-ui/scripts/_completion $fpath)
#     autoload -U compinit; compinit
#
# Then `pandamonium <tab>` completes subcommands; `pandamonium mail <tab>`
# completes mail subcommands; `pandamonium-mail <tab>` works the same.

_pandamonium_scripts_dir() {
    local self="${(%):-%x}"
    while [[ -L "$self" ]]; do self="$(readlink "$self")"; done
    cd "${self:h}/.." && pwd
}

typeset -gA _pandamonium_subs

_pandamonium_refresh() {
    _pandamonium_subs=()
    local dir="$(_pandamonium_scripts_dir)"
    local py="$dir/../venv/bin/python"
    [[ -x "$py" ]] || py="$(command -v python3)"
    local f sub help_out commands
    for f in "$dir"/pandamonium-*; do
        [[ -x "$f" ]] || continue
        case "$f" in
            *.bak|*.pyc|*.pre-*) continue ;;
        esac
        sub="${${f:t}#pandamonium-}"
        help_out=$("$py" "$f" --help 2>/dev/null) || continue
        commands=$(echo "$help_out" | grep -oE '\{[a-z0-9_,-]+\}' | head -1 \
            | tr -d '{}' | tr ',' ' ')
        _pandamonium_subs[$sub]="$commands"
    done
}

_pandamonium() {
    [[ ${#_pandamonium_subs} -eq 0 ]] && _pandamonium_refresh

    local cmd="${words[1]}"

    if [[ "$cmd" == "pandamonium" ]]; then
        if (( CURRENT == 2 )); then
            local -a subs=(${(k)_pandamonium_subs} help)
            _describe 'subcommand' subs
            return
        fi
        local sub="${words[2]}"
        if [[ "$sub" == "help" ]] && (( CURRENT == 3 )); then
            local -a subs=(${(k)_pandamonium_subs})
            _describe 'subcommand' subs
            return
        fi
        if (( CURRENT == 3 )); then
            local -a sc=(${(s/ /)_pandamonium_subs[$sub]})
            _describe 'command' sc
            return
        fi
        return
    fi

    # pandamonium-foo <tab>
    local sub="${cmd#pandamonium-}"
    if (( CURRENT == 2 )); then
        local -a sc=(${(s/ /)_pandamonium_subs[$sub]})
        _describe 'command' sc
        return
    fi
}

_pandamonium "$@"
