_codex_sessions_complete()
{
    local cur="${COMP_WORDS[COMP_CWORD]}" prev="${COMP_WORDS[COMP_CWORD-1]}"
    local command="" command_index=0 word candidate i
    local options="--home --socket --codex-url --codex-token-file --port --address-port --data-dir --json --help -h"
    COMPREPLY=()

    case "$prev" in
        --home|--data-dir)
            while IFS= read -r candidate; do COMPREPLY+=("$candidate"); done < <(compgen -d -- "$cur")
            compopt -o filenames 2>/dev/null || true
            return
            ;;
        --socket|--codex-token-file|--json)
            while IFS= read -r candidate; do COMPREPLY+=("$candidate"); done < <(compgen -f -- "$cur")
            compopt -o filenames 2>/dev/null || true
            return
            ;;
        --port|--address-port|--codex-url) return ;;
    esac

    for ((i = 1; i < COMP_CWORD; i++)); do
        word="${COMP_WORDS[i]}"
        case "$word" in
            --home|--socket|--codex-url|--codex-token-file|--port|--address-port|--data-dir|--json) ((i++)) ;;
            -*) ;;
            *) command="$word"; command_index=$i; break ;;
        esac
    done

    local choices="$options"
    if [[ "$cur" != -* ]]; then
        case "$command" in
            "") choices="start stop restart status serve route mcp call models completion install-claude-context $options" ;;
            completion)
                if ((COMP_CWORD == command_index + 1)); then choices="bash"; fi
                ;;
            call)
                if ((COMP_CWORD == command_index + 1)); then
                    choices="protocol.guide models.list agents.launch agents.list agents.send agents.steer agents.status agents.wait agents.result agents.stop agents.cancel agents.children agents.transcript artifacts.put artifacts.get"
                fi
                ;;
        esac
    fi
    while IFS= read -r candidate; do COMPREPLY+=("$candidate"); done < <(compgen -W "$choices" -- "$cur")
}

complete -F _codex_sessions_complete claudex codex-sessions codex-session
