"""Portable caller context. Harness-specific settings are reported, not silently impersonated."""
import json
import re
import os
import subprocess
from pathlib import Path

from .inheritance import validate_conversation
from .platform import quiet_process_options

PORTABLE = {"model", "effortLevel", "language", "outputStyle", "permissions", "enabledPlugins"}


def _imports(text):
    lines, fence = [], None
    for line in text.splitlines(keepends=True):
        marker = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if marker:
            token = marker.group(1)
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
            lines.append("\n")
        else:
            lines.append("\n" if fence else line)
    prose = re.sub(r"(`+)(?!`)[\s\S]*?(?<!`)\1(?!`)", "", "".join(lines))
    for match in re.finditer(r'''(?<![\w@\\])@((?:\\ |[^\s`'"<>\[\]()])+)(?=$|[\s\[\]()])''', prose):
        value = match.group(1).replace("\\ ", " ").rstrip(".,;:!?")
        if value:
            yield value


def _sensitive(path):
    name = path.name.lower()
    private_directories = {".ssh", ".aws", ".gnupg", ".azure", ".kube", ".docker", ".git"}
    private_configs = {"auth.json", ".credentials.json", "credentials", "credentials.json", ".claude.json", ".mcp.json"}
    return (any(part.lower() in private_directories for part in path.parts)
            or name == ".env" or name.startswith(".env.") or name in private_configs
            or path.suffix.lower() in {".pem", ".key"})


def _working_tree_root(cwd):
    try:
        found = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=cwd,
                               capture_output=True, text=True, timeout=5, check=False, **quiet_process_options())
        if found.returncode == 0:
            return Path(found.stdout.strip()).resolve()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return cwd


def _repository_root(cwd):
    try:
        found = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=cwd,
                               capture_output=True, text=True, timeout=5, check=False, **quiet_process_options())
        if found.returncode == 0:
            common = Path(found.stdout.strip()).resolve()
            if common.name == ".git":
                return common.parent
            found = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=cwd,
                                   capture_output=True, text=True, timeout=5, check=False, **quiet_process_options())
            if found.returncode == 0:
                return Path(found.stdout.strip()).resolve()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return cwd


def _markdown_files(root, warnings, allowed):
    visited = set()
    def unreadable(error):
        warnings.append(f"Could not inspect context directory {error.filename}")
    for directory, children, filenames in os.walk(root, followlinks=True, onerror=unreadable):
        if not allowed(Path(directory)):
            children[:] = []
            continue
        resolved = Path(directory).resolve()
        if resolved in visited:
            children[:] = []
            continue
        visited.add(resolved)
        children[:] = sorted(name for name in children if allowed(Path(directory) / name))
        for name in sorted(filenames):
            if name.endswith(".md"):
                yield Path(directory) / name


def merge_settings(base, extra):
    for settings in (base, extra):
        for key in ("enabledPlugins", "permissions"):
            if key in settings and not isinstance(settings[key], dict):
                raise ValueError(f"Claude {key} settings must be an object")
        for key in ("allow", "deny", "ask"):
            value = settings.get("permissions", {}).get(key, [])
            if not isinstance(value, list) or any(not isinstance(rule, str) for rule in value):
                raise ValueError(f"Claude permissions.{key} must be a list of strings")
    combined = {**base, **extra}
    combined["enabledPlugins"] = {**base.get("enabledPlugins", {}), **extra.get("enabledPlugins", {})}
    permissions = {**base.get("permissions", {}), **extra.get("permissions", {})}
    for field in ("allow", "deny", "ask"):
        values = list(base.get("permissions", {}).get(field, [])) + list(extra.get("permissions", {}).get(field, []))
        if values:
            permissions[field] = list(dict.fromkeys(values))
    if permissions:
        combined["permissions"] = permissions
    if not combined["enabledPlugins"]:
        combined.pop("enabledPlugins")
    return combined


def capture(cwd, home=None, codex_home=None, claude_home=None):
    explicit_home = home is not None
    cwd, home = Path(cwd).resolve(), Path(home or Path.home()).resolve()
    codex_root = Path(codex_home or (home / ".codex" if explicit_home else os.environ.get("CODEX_HOME", home / ".codex"))).expanduser().resolve()
    claude_root = Path(claude_home or (home / ".claude" if explicit_home else os.environ.get("CLAUDE_CONFIG_DIR", home / ".claude"))).expanduser().resolve()
    project_root = _working_tree_root(cwd)
    result = {"provider": "local", "cwd": str(cwd), "instructions": [], "memories": [], "skills": [], "settings": {}, "unsupported_settings": [], "warnings": [], "settings_sources": [], "memory_roots": []}
    private_configs = {claude_root / name for name in ("settings.json", "settings.local.json", ".credentials.json")}
    user_instructions = {home / "CLAUDE.md", claude_root / "CLAUDE.md", codex_root / "AGENTS.md"}
    if not explicit_home:
        user_instructions.add(Path("/etc/claude-code/CLAUDE.md"))
    user_scopes = [claude_root / "rules", claude_root / "skills", home / ".agents/skills", codex_root / "skills", codex_root / "memories"]
    chain = list(reversed([cwd, *cwd.parents]))
    if explicit_home:
        try:
            boundary = Path(os.path.commonpath([home, cwd]))
        except ValueError:
            boundary = project_root
        chain = [directory for directory in chain if directory.is_relative_to(boundary)]
    ancestor_rules = [directory / ".claude/rules" for directory in chain if directory / ".claude/rules" != claude_root / "rules"]
    ancestor_files = {directory / name for directory in chain for name in ("CLAUDE.md", "CLAUDE.local.md", "AGENTS.md", ".claude/CLAUDE.md")} - user_instructions
    import_scopes = [project_root, *user_scopes]
    seen, emitted = set(), set()

    def allowed(path, roots=(), exact=(), settings=False):
        path = Path(os.path.abspath(path))
        try:
            resolved = path.resolve()
        except (OSError, RuntimeError):
            result["warnings"].append(f"Could not resolve context path {path}")
            return False
        if (_sensitive(path) or _sensitive(resolved)
                or (not settings and (resolved in private_configs or path.name.startswith("settings") and path.suffix == ".json"))):
            result["warnings"].append(f"Skipped sensitive context import {path}")
            return False
        if not any(resolved.is_relative_to(root) for root in roots) and resolved not in exact:
            result["warnings"].append(f"Skipped context path outside allowed context roots: {path}")
            return False
        return True

    def read(path, roots=(), exact=(), settings=False):
        if not allowed(path, roots, exact, settings):
            return None
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            result["warnings"].append(f"Could not read {path}")
            return None
        if re.search(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----|PuTTY-User-Key-File-[0-9]+:", text):
            result["warnings"].append(f"Skipped context file containing private-key content: {path}")
            return None
        return text

    def add(path, field, condition=None, stack=(), imported_from=None, trusted=False, roots=None):
        path = Path(os.path.abspath(path))
        roots = import_scopes if roots is None else roots
        if not path.exists() and not path.is_symlink():
            if imported_from:
                result["warnings"].append(f"Could not read context import {path} from {imported_from}")
            return
        exact = (path,) if not imported_from else ()
        if not allowed(path, roots, exact):
            return
        resolved = path.resolve()
        trusted = (trusted and not resolved.is_relative_to(project_root) and resolved not in ancestor_files
                   and not any(resolved.is_relative_to(root) for root in ancestor_rules))
        if resolved in stack:
            result["warnings"].append(f"Context import cycle at {path}")
            return
        if len(stack) > 4:
            result["warnings"].append(f"Context import depth exceeds four hops at {path}")
            return
        key = (resolved, json.dumps(condition, sort_keys=True))
        traversal = (*key, trusted)
        if traversal in seen:
            return
        seen.add(traversal)
        text = read(path, roots, exact)
        if text is None:
            return
        item = {"source": str(resolved), "text": text}
        if condition:
            item["condition"] = condition
        if imported_from:
            item["imported_from"] = str(imported_from)
        if key not in emitted:
            result[field].append(item)
            emitted.add(key)
        if field == "instructions":
            result["provider"] = "claude"
            for value in _imports(text):
                imported = home / value[2:] if value.startswith("~/") else Path(value)
                if not imported.is_absolute():
                    imported = path.parent / imported
                imported = Path(os.path.abspath(imported))
                if "/" not in value and "." not in value and not imported.is_file():
                    continue
                child_roots = ([*import_scopes, *roots, imported.parent] if trusted else
                               [*import_scopes, *(root for root in roots if root in ancestor_rules)])
                add(imported, field, condition, (*stack, resolved), resolved, trusted, child_roots)

    def add_rules(root, base, trusted=False):
        if not root.is_dir():
            return
        scopes = [root]
        # A selected rules directory is a scope, never a grant to its symlink target.
        if not allowed(root, scopes):
            return
        for path in _markdown_files(root, result["warnings"], lambda path: allowed(path, scopes)):
            text = read(path, scopes)
            if text is None:
                continue
            frontmatter = re.match(r"\A---\s*\n(.*?)\n---\s*(?:\n|$)", text, re.DOTALL)
            condition = None
            if frontmatter and re.search(r"^paths\s*:", frontmatter.group(1), re.MULTILINE):
                condition = {"base": str(base), "paths_frontmatter": frontmatter.group(1)}
            add(path, "instructions", condition, trusted=trusted, roots=scopes)

    for path in sorted(user_instructions):
        add(path, "instructions", trusted=True)
    add_rules(claude_root / "rules", cwd, trusted=True)
    skill_roots = [(root, [root]) for root in (claude_root / "skills", home / ".agents/skills", codex_root / "skills")]
    user_settings = {claude_root / "settings.json", claude_root / "settings.local.json"}
    setting_files = list(sorted(user_settings))
    for directory in chain:
        for name in ("CLAUDE.md", "CLAUDE.local.md", "AGENTS.md", ".claude/CLAUDE.md"):
            path = directory / name
            if path not in user_instructions:
                add(path, "instructions")
        if directory / ".claude/rules" != claude_root / "rules":
            add_rules(directory / ".claude/rules", directory)
        skill_roots += [(directory / name, [directory / name]) for name in (".claude/skills", ".agents/skills", ".codex/skills")]
        setting_files += [directory / ".claude/settings.json", directory / ".claude/settings.local.json"]
    shared_root = _repository_root(cwd)
    project_name = os.environ.get("CLAUDE_CODE_PROJECT_DIR_NAME") if not explicit_home else None
    if project_name and (Path(project_name).name != project_name or project_name in {".", ".."}):
        raise ValueError("CLAUDE_CODE_PROJECT_DIR_NAME must be a directory name")
    slugs = [project_name] if project_name else [re.sub(r"[^a-zA-Z0-9]", "-", str(shared_root))]
    legacy_slug = str(shared_root).replace("/", "-")
    if not project_name and not re.search(r"[\\/:]", legacy_slug) and legacy_slug not in slugs:
        slugs.append(legacy_slug)
    memory_roots = [claude_root / "projects" / slug / "memory" for slug in slugs]
    memory_scopes = [project_root, *memory_roots, codex_root / "memories"]
    for path in dict.fromkeys(setting_files):
        if not path.is_file():
            continue
        text = read(path, exact=(path,), settings=True)
        if text is None:
            continue
        try:
            settings = json.loads(text)
            if not isinstance(settings, dict):
                raise ValueError("Settings must be an object")
            result["settings"] = merge_settings(result["settings"], {key: value for key, value in settings.items() if key in PORTABLE})
            if "autoMemoryDirectory" in settings:
                value = settings["autoMemoryDirectory"]
                if not isinstance(value, str) or not (Path(value).is_absolute() or value.startswith("~/")):
                    raise ValueError("autoMemoryDirectory must be an absolute path or start with ~/")
                memory = Path(os.path.abspath(home / value[2:] if value.startswith("~/") else value))
                scopes = [memory] if path in user_settings else memory_scopes
                if allowed(memory, scopes):
                    memory_roots = [memory]
                    if path in user_settings:
                        memory_scopes.append(memory)
                else:
                    result["warnings"].append(f"Skipped unsafe autoMemoryDirectory in {path}")
            result["unsupported_settings"].extend(key for key in settings if key not in PORTABLE and key != "autoMemoryDirectory")
            result["settings_sources"].append(str(path.resolve()))
            result["provider"] = "claude"
        except (OSError, ValueError):
            result["warnings"].append(f"Could not read settings from {path}")
    if result["settings"].get("permissions"):
        result["warnings"].append("Inherited Claude permission rules are context only; Codex cannot enforce Claude tool permission syntax.")
    for memory in [*memory_roots, codex_root / "memories"]:
        if memory.is_dir() and allowed(memory, memory_scopes):
            result["memory_roots"].append(str(memory.resolve()))
            for path in _markdown_files(memory, result["warnings"], lambda path: allowed(path, [memory])):
                add(path, "memories", roots=[memory])
    manifest = claude_root / "plugins/installed_plugins.json"
    if manifest.is_file():
        text = read(manifest, roots=[claude_root / "plugins"])
        try:
            installed = json.loads(text).get("plugins", {}) if text is not None else {}
            enabled = result["settings"].get("enabledPlugins", {})
            for name, versions in installed.items():
                if enabled.get(name) and versions:
                    path = versions[-1].get("installPath")
                    if path:
                        root = Path(os.path.abspath(Path(path).expanduser())) / "skills"
                        skill_roots.append((root, [root]))
        except (OSError, ValueError, TypeError):
            result["warnings"].append("The enabled-plugin skill inventory could not be read")
    skill_paths = set()
    for root, scopes in skill_roots:
        if not root.is_dir() or not allowed(root, scopes):
            continue
        for path in [*root.glob("*/SKILL.md"), *root.glob("SKILL.md")]:
            if not allowed(path, scopes):
                continue
            resolved = path.resolve()
            if resolved in skill_paths:
                continue
            skill_paths.add(resolved)
            body = read(path, scopes)
            if body is None:
                continue
            name = re.search(r"^name:\s*(.+)$", body, re.MULTILINE)
            description = re.search(r"^description:\s*(.+)$", body, re.MULTILINE)
            detail = description.group(1).strip().strip("\"'") if description else "Read this skill's instructions before applying it."
            if detail.startswith(("|", ">")) and description:
                lines = []
                for line in body[description.end():].splitlines():
                    if line.strip() and not line.startswith(" "):
                        break
                    if line.strip():
                        lines.append(line.strip())
                detail = " ".join(lines)
            result["skills"].append({"name": name.group(1).strip().strip("\"'") if name else path.parent.name, "description": detail, "path": str(resolved)})
    result["unsupported_settings"] = sorted(set(result["unsupported_settings"]))
    result["warnings"] = list(dict.fromkeys(result["warnings"]))
    if len(render(result)) > 400_000:
        raise ValueError("The inherited context is too large. Supply an explicit context bundle or set inherit_context=false; nothing was truncated.")
    return result


def render(context):
    sections = ["Inherited caller context. Explicit instructions in the current task take precedence over inherited preferences. Apply saved preferences within the native Codex sandbox. Claude-specific hooks, slash commands, and tool permission syntax are not automatically executed or enforced by Codex. Do not assume tools or credentials exist merely because they were available to the caller."]
    for field in ("instructions", "memories"):
        for item in context.get(field, []):
            condition = item.get("condition")
            scope = f"Apply only when working with files matching this rule's paths frontmatter, relative to {condition['base']}.\n" if condition else ""
            sections.append(f"{field}: {item['source']}\n{scope}{item['text']}")
    if context.get("session_summary"):
        sections.append("Caller-provided current-session summary:\n" + context["session_summary"])
    if context.get("skills"):
        sections.append("Available inherited skills. Read the SKILL.md at the listed path before applying a skill; resolve its resources relative to that path. Map tool names to the native Codex equivalents when appropriate.\n" + "\n".join(f"- {s['name']}: {s.get('description', '')} (file: {s['path']})" for s in context["skills"]))
    if context.get("settings"):
        sections.append("Portable caller preferences (native runtime permissions remain authoritative):\n" + json.dumps(context["settings"], ensure_ascii=False))
    if context.get("warnings"):
        sections.append("Context capture notices:\n" + "\n".join(context["warnings"]))
    if context.get("provider") == "claude":
        sections.append("Translate Claude tool references by capability: use native file tools for Read/Write/Edit, shell execution for Bash, file search for Grep/Glob, and agents_launch for delegation. Read applicable nested CLAUDE.md, CLAUDE.local.md and .claude/rules before working in a subdirectory. Skill paths and their relative resources refer to the inherited files. Historical caller messages are supplied separately as reference evidence.")
    return "\n\n".join(sections)


def merge(base, supplied):
    if not isinstance(supplied, dict):
        raise ValueError("context must be an object")
    result = {**base}
    for field in ("instructions", "memories", "skills"):
        values = supplied.get(field, [])
        key = "path" if field == "skills" else "source"
        if not isinstance(values, list) or any(not isinstance(item, dict) or not isinstance(item.get(key), str) or (field != "skills" and not isinstance(item.get("text"), str)) for item in values):
            raise ValueError(f"context.{field} must contain named source objects with text")
        result[field] = list({(item[key], json.dumps(item.get("condition"), sort_keys=True)): item
                              for item in [*base.get(field, []), *values]}.values())
    settings = supplied.get("settings", {})
    if not isinstance(settings, dict):
        raise ValueError("context.settings must be an object")
    result["settings"] = merge_settings(base.get("settings", {}), {key: value for key, value in settings.items() if key in PORTABLE})
    result["unsupported_settings"] = sorted(set(base.get("unsupported_settings", []) + supplied.get("unsupported_settings", []) + [key for key in settings if key not in PORTABLE]))
    if supplied.get("session_summary"):
        if not isinstance(supplied["session_summary"], str):
            raise ValueError("context.session_summary must be text")
        result["session_summary"] = supplied["session_summary"]
    result["warnings"] = base.get("warnings", []) + supplied.get("warnings", [])
    for field in ("settings_sources", "memory_roots"):
        result[field] = list(dict.fromkeys([*base.get(field, []), *supplied.get(field, [])]))
    result["provider"] = supplied.get("provider", base.get("provider", "caller"))
    if "caller" in supplied:
        if not isinstance(supplied["caller"], dict):
            raise ValueError("context.caller must be an object")
        result["caller"] = supplied["caller"]
    if "conversation" in supplied:
        result["conversation"] = validate_conversation(supplied["conversation"])
    if "export_id" in supplied:
        if not isinstance(supplied["export_id"], str) or not re.fullmatch(r"[0-9a-f]{64}", supplied["export_id"]):
            raise ValueError("context.export_id must identify a context export")
        result["export_id"] = supplied["export_id"]
    if len(render(result)) > 400_000:
        raise ValueError("The inherited context is too large; nothing was truncated")
    return result


def report(context):
    return {"provider": context.get("provider", "caller"), "instruction_files": len(context.get("instructions", [])), "memory_files": len(context.get("memories", [])), "skills": len(context.get("skills", [])),
            "settings": list(context.get("settings", {})), "has_session_summary": bool(context.get("session_summary")), "unsupported_settings": context.get("unsupported_settings", []), "warnings": context.get("warnings", []),
            "sources": [item["source"] for field in ("instructions", "memories") for item in context.get(field, [])],
            "settings_sources": context.get("settings_sources", []), "memory_roots": context.get("memory_roots", []),
            "conditional_rules": sum(bool(item.get("condition")) for item in context.get("instructions", [])),
            "imported_files": sum(bool(item.get("imported_from")) for item in context.get("instructions", [])),
            "caller_session_id": context.get("caller", {}).get("session_id"),
            "conversation_messages": context.get("conversation", {}).get("message_count", 0),
            "conversation_sha256": context.get("conversation", {}).get("content_sha256"),
            "conversation_snapshot": bool(context.get("conversation", {}).get("snapshot")),
            "export_id": context.get("export_id")}
