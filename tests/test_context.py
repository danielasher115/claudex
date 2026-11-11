import json
import re
import subprocess
import sys
import pytest
from codex_sessions.context import capture, merge, render, report


@pytest.fixture
def symlink():
    def create(link, target, directory=False):
        try:
            link.symlink_to(target, target_is_directory=directory)
        except OSError as error:
            if sys.platform == "win32" and getattr(error, "winerror", None) == 1314:
                pytest.skip("Windows symlink creation requires Developer Mode or elevated privileges")
            raise
    return create


def test_merge_preserves_distinct_scopes_for_the_same_imported_instruction():
    shared = {'source': '/project/shared.md', 'text': 'The shared rule'}
    scoped = {**shared, 'condition': {'base': '/project', 'paths_frontmatter': 'paths: ["src/**"]'}}
    context = merge({'instructions': [shared]}, {'instructions': [scoped]})
    assert len(context['instructions']) == 2
    assert context['instructions'][0] == shared
    assert context['instructions'][1] == scoped


def test_custom_claude_config_settings_are_not_imported_as_rule_text(tmp_path):
    home = tmp_path / 'home'; home.mkdir()
    project = tmp_path / 'repo'; project.mkdir()
    config = tmp_path / 'custom-config'; config.mkdir()
    (config / 'settings.json').write_text(json.dumps({'model': 'sonnet', 'env': {'TOKEN': 'secret-custom-env'}}))
    (home / '.claude.json').write_text('{"token":"secret-home-config"}')
    (project / 'CLAUDE.md').write_text(f'@{config}/settings.json\n@~/.claude.json\nKeep public rules.')
    result = capture(project, home=home, claude_home=config)
    assert 'Keep public rules.' in render(result)
    assert result['settings']['model'] == 'sonnet'
    assert 'secret-custom-env' not in json.dumps(result)
    assert 'secret-home-config' not in json.dumps(result)


def test_capture_includes_instructions_skills_and_project_memory_without_env_secrets(tmp_path):
    home = tmp_path / "home"; project = tmp_path / "repo"
    (home / ".claude/skills/review").mkdir(parents=True)
    project.mkdir()
    (home / ".claude/CLAUDE.md").write_text("Global writing preference")
    (home / ".claude/settings.json").write_text(json.dumps({"model": "sonnet", "env": {"API_TOKEN": "never-copy-this-value"}, "hooks": {"SessionStart": []}}))
    (home / ".claude/skills/review/SKILL.md").write_text("---\nname: reviewer\ndescription: Review changes carefully\n---\nRead the code")
    (project / "CLAUDE.md").write_text("Project instruction")
    memory = home / ".claude/projects" / re.sub(r"[^a-zA-Z0-9]", "-", str(project)) / "memory"
    memory.mkdir(parents=True)
    (memory / "MEMORY.md").write_text("Remember the integration contract")
    context = capture(project, home=home)
    text = render(context)
    assert "Global writing preference" in text and "Project instruction" in text
    assert "reviewer" in text and "SKILL.md" in text
    assert "Remember the integration contract" in text
    assert "never-copy-this-value" not in json.dumps(context)
    assert "hooks" in report(context)["unsupported_settings"]
    assert context["settings"]["model"] == "sonnet"


def test_project_settings_preserve_global_plugin_and_permission_entries(tmp_path):
    home = tmp_path / "home"; project = tmp_path / "repo"
    (home / ".claude").mkdir(parents=True); (project / ".claude").mkdir(parents=True)
    (home / ".claude/settings.json").write_text(json.dumps({"enabledPlugins": {"alpha": True, "beta": True}, "permissions": {"deny": ["Read(.env)"]}}))
    (project / ".claude/settings.json").write_text(json.dumps({"enabledPlugins": {"beta": False}, "permissions": {"allow": ["Read(src/**)"]}}))
    context = capture(project, home=home)
    assert context["settings"]["enabledPlugins"] == {"alpha": True, "beta": False}
    assert context["settings"]["permissions"] == {"deny": ["Read(.env)"], "allow": ["Read(src/**)"]}


def test_explicit_codex_home_does_not_capture_the_other_home(tmp_path):
    home = tmp_path / "home"; selected = tmp_path / "selected"; project = tmp_path / "repo"
    (home / ".codex").mkdir(parents=True); selected.mkdir(); project.mkdir()
    (home / ".codex/AGENTS.md").write_text("Wrong home")
    (selected / "AGENTS.md").write_text("Selected home")
    context = capture(project, home=home, codex_home=selected)
    text = render(context)
    assert "Selected home" in text and "Wrong home" not in text


def test_capture_loads_home_rules_and_all_ancestors_with_conditional_scope(tmp_path):
    home = tmp_path / "home"
    project = tmp_path / "outside"
    launch = project.joinpath(*[f"level{i}" for i in range(12)])
    launch.mkdir(parents=True)
    (home / ".claude/rules").mkdir(parents=True)
    (home / "CLAUDE.md").write_text("Home root preference")
    (home / ".claude/rules/writing.md").write_text("User rule")
    (project / "CLAUDE.md").write_text("Deep ancestor rule")
    (project / ".claude/rules").mkdir(parents=True)
    (project / ".claude/rules/api.md").write_text('---\npaths:\n  - "src/api/**/*.py"\n---\nValidate endpoints')
    context = capture(launch, home=home)
    text = render(context)
    assert all(value in text for value in ("Home root preference", "User rule", "Deep ancestor rule"))
    rule = next(item for item in context["instructions"] if item["source"].endswith("api.md"))
    assert rule["condition"]["base"] == str(project)
    assert "src/api/**/*.py" in rule["condition"]["paths_frontmatter"]
    assert "Apply only" in text
    assert report(context)["conditional_rules"] == 1


def test_capture_expands_relative_imports_without_code_examples_mentions_or_secrets(tmp_path):
    home = tmp_path / "home"
    project = tmp_path / "repo"
    (project / "docs/Design Docs").mkdir(parents=True)
    (home / ".claude").mkdir(parents=True)
    (home / "preferences.md").write_text("Imported home preference")
    (project / "CLAUDE.md").write_text(
        "Read @docs/rules.md and @~/preferences.md.\n"
        "Mention @teammate and user@example.com.\n"
        "`@inline.md`\n```md\n@fenced.md\n```\n"
        "@.env\n@auth.json\n"
    )
    (project / "docs/rules.md").write_text("Nested import @Design\\ Docs/more.md")
    (project / "docs/Design Docs/more.md").write_text("Nested imported preference")
    (project / "inline.md").write_text("INLINE EXAMPLE MUST NOT LOAD")
    (project / "fenced.md").write_text("FENCED EXAMPLE MUST NOT LOAD")
    (project / ".env").write_text("SENSITIVE ENV VALUE")
    (project / "auth.json").write_text("SENSITIVE AUTH VALUE")
    context = capture(project, home=home)
    text = render(context)
    assert "Nested imported preference" in text and "Imported home preference" not in text
    assert any("outside allowed context" in warning for warning in context["warnings"])
    assert not any(value in text for value in ("INLINE EXAMPLE MUST NOT LOAD", "FENCED EXAMPLE MUST NOT LOAD", "SENSITIVE ENV VALUE", "SENSITIVE AUTH VALUE"))
    assert not any("teammate" in warning or "example.com" in warning for warning in context["warnings"])
    assert next(item for item in context["instructions"] if item["source"].endswith("more.md"))["imported_from"].endswith("rules.md")


def test_capture_reports_import_cycles_missing_files_and_depth_limit(tmp_path):
    home = tmp_path / "home"; project = tmp_path / "repo"
    home.mkdir(); project.mkdir()
    (project / "CLAUDE.md").write_text("@one.md\n@missing.md")
    (project / "one.md").write_text("@CLAUDE.md\n@two.md")
    (project / "two.md").write_text("@three.md")
    (project / "three.md").write_text("@four.md")
    (project / "four.md").write_text("@five.md")
    (project / "five.md").write_text("TOO DEEP")
    context = capture(project, home=home)
    assert "TOO DEEP" not in render(context)
    notices = " ".join(context["warnings"]).lower()
    assert "cycle" in notices and "missing.md" in notices and "depth" in notices


def test_capture_uses_shared_git_worktree_memory_with_slug_punctuation(tmp_path):
    home = tmp_path / "home"; project = tmp_path / "repo with.dots"
    home.mkdir(); project.mkdir()
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    subprocess.run(["git", "-C", str(project), "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "--allow-empty", "-qm", "initial"], check=True)
    worktree = tmp_path / "worktree"
    subprocess.run(["git", "-C", str(project), "worktree", "add", "-qb", "test", str(worktree)], check=True)
    launch = worktree / "nested"; launch.mkdir()
    memory = home / ".claude/projects" / re.sub(r"[^a-zA-Z0-9]", "-", str(project)) / "memory"
    memory.mkdir(parents=True)
    (memory / "MEMORY.md").write_text("Shared worktree memory")
    unrelated = home / ".claude/projects/unrelated/memory"; unrelated.mkdir(parents=True)
    (unrelated / "MEMORY.md").write_text("Unrelated project memory")
    context = capture(launch, home=home)
    assert "Shared worktree memory" in render(context)
    assert "Unrelated project memory" not in render(context)
    assert str(memory) in report(context)["memory_roots"]


def test_capture_uses_local_settings_memory_override_and_reports_permission_limits(tmp_path):
    home = tmp_path / "home"; project = tmp_path / "repo"
    (home / ".claude").mkdir(parents=True); (project / ".claude").mkdir(parents=True)
    custom = home / "shared-memory"; custom.mkdir()
    (custom / "MEMORY.md").write_text("Custom memory preference")
    (home / ".claude/settings.json").write_text(json.dumps({"autoMemoryDirectory": "~/shared-memory", "permissions": {"deny": ["Read(.env)"]}}))
    local = project / ".claude/settings.local.json"
    local.write_text(json.dumps({"autoMemoryDirectory": "~/shared-memory", "env": {"TOKEN": "NEVER COPY"}}))
    context = capture(project, home=home)
    assert "Custom memory preference" in render(context)
    assert str(local) in report(context)["settings_sources"]
    assert any("permission" in warning.lower() and "enforce" in warning.lower() for warning in context["warnings"])
    assert "NEVER COPY" not in json.dumps(context)


def test_capture_fails_instead_of_truncating_large_rules(tmp_path):
    home = tmp_path / "home"; project = tmp_path / "repo"
    home.mkdir(); project.mkdir()
    (project / "CLAUDE.md").write_text("x" * 400_001)
    with pytest.raises(ValueError, match="nothing was truncated"):
        capture(project, home=home)


def test_capture_blocks_rule_symlink_escape(tmp_path, symlink):
    home = tmp_path / "home"; project = tmp_path / "repo"
    (home / ".claude/rules").mkdir(parents=True); project.mkdir()
    shared = tmp_path / "shared"; shared.mkdir()
    (shared / "rule.md").write_text("Shared rule @README")
    (shared / "README").write_text("Bare filename import")
    symlink(home / ".claude/rules/shared", shared, directory=True)
    symlink(shared / "cycle", home / ".claude/rules", directory=True)
    context = capture(project, home=home)
    assert "Shared rule" not in render(context) and "Bare filename import" not in render(context)
    reason = "reparse point" if sys.platform == "win32" else "outside allowed context"
    assert any(reason in warning and "shared" in warning for warning in context["warnings"])


def test_capture_keeps_conditional_imports_scoped_and_notices_visible_to_agent(tmp_path):
    home = tmp_path / "home"; project = tmp_path / "repo"
    home.mkdir(); (project / ".claude/rules").mkdir(parents=True)
    (project / ".claude/rules/api.md").write_text('---\npaths: "src/**/*.py"\n---\n@detail.md\n@missing.md')
    (project / ".claude/rules/detail.md").write_text("Imported scoped instruction")
    context = capture(project, home=home)
    imported = next(item for item in context["instructions"] if item.get("imported_from"))
    assert imported["condition"]["base"] == str(project)
    assert "Could not read context import" in render(context)


def test_user_conditional_rules_keep_launch_base_when_project_is_under_home(tmp_path):
    home = tmp_path / "home"; project = home / "repo"
    project.mkdir(parents=True); (home / ".claude/rules").mkdir(parents=True)
    (home / ".claude/rules/python.md").write_text('---\npaths: "**/*.py"\n---\nUser Python rule')
    context = capture(project, home=home)
    rules = [item for item in context["instructions"] if item["source"].endswith("python.md")]
    assert len(rules) == 1
    assert rules[0]["condition"]["base"] == str(project)


@pytest.mark.parametrize("target", [".ssh/id_ed25519", ".aws/credentials", ".gnupg/notes.md", "private-notes.md"])
def test_project_imports_cannot_read_arbitrary_home_files(tmp_path, target):
    home = tmp_path / "home"; project = tmp_path / "repo"
    project.mkdir(); (home / target).parent.mkdir(parents=True, exist_ok=True)
    (home / target).write_text("UNRELATED PRIVATE FILE")
    (project / "CLAUDE.md").write_text(f"@~/{target}")
    context = capture(project, home=home)
    assert "UNRELATED PRIVATE FILE" not in json.dumps(context)
    assert context["warnings"]


def test_user_instruction_can_explicitly_import_document_without_elevating_project(tmp_path):
    home = tmp_path / "home"; project = tmp_path / "repo"
    home.mkdir(); project.mkdir()
    (home / "preferences.md").write_text("Explicit user preference")
    (home / "secret.md").write_text("UNRELATED USER SECRET")
    (home / "CLAUDE.md").write_text(f"@~/preferences.md\n@{project}/CLAUDE.md")
    (project / "CLAUDE.md").write_text("@~/secret.md")
    context = capture(project, home=home)
    assert "Explicit user preference" in render(context)
    assert "UNRELATED USER SECRET" not in json.dumps(context)


def test_project_memory_override_cannot_select_home_directory(tmp_path):
    home = tmp_path / "home"; project = tmp_path / "repo"
    home.mkdir(); (project / ".claude").mkdir(parents=True)
    (home / "notes.md").write_text("UNRELATED MEMORY SECRET")
    (project / ".claude/settings.json").write_text(json.dumps({"autoMemoryDirectory": str(home)}))
    context = capture(project, home=home)
    assert "UNRELATED MEMORY SECRET" not in json.dumps(context)
    assert any("autoMemoryDirectory" in warning for warning in context["warnings"])


@pytest.mark.parametrize("kind", ["instructions", "rules", "memories", "skills", "settings"])
def test_project_context_symlinks_cannot_escape_to_home(tmp_path, kind, symlink):
    home = tmp_path / "home"; project = tmp_path / "repo"
    home.mkdir(); (project / ".claude").mkdir(parents=True)
    secret = home / "private.md"
    secret.write_text('name: leaked\ndescription: UNRELATED SYMLINK SECRET\n{"model":"UNRELATED SYMLINK SECRET"}')
    if kind == "instructions":
        link = project / "CLAUDE.md"
    elif kind == "rules":
        link = project / ".claude/rules/rule.md"
    elif kind == "memories":
        link = project / "memory/MEMORY.md"
        (project / ".claude/settings.json").write_text(json.dumps({"autoMemoryDirectory": str(project / "memory")}))
    elif kind == "skills":
        link = project / ".claude/skills/example/SKILL.md"
    else:
        secret.write_text('{"model":"UNRELATED SYMLINK SECRET"}')
        link = project / ".claude/settings.json"
    link.parent.mkdir(parents=True, exist_ok=True)
    symlink(link, secret)
    context = capture(project, home=home)
    assert "UNRELATED SYMLINK SECRET" not in json.dumps(context)
    assert context["warnings"]


@pytest.mark.parametrize("kind", ["rules", "memories", "skills", "instructions"])
def test_private_key_content_is_not_included_even_in_public_document(tmp_path, kind):
    home = tmp_path / "home"; project = tmp_path / "repo"
    home.mkdir(); (project / ".claude").mkdir(parents=True)
    key = "-----BEGIN OPENSSH PRIVATE KEY-----\nSYNTHETIC PRIVATE KEY BODY\n-----END OPENSSH PRIVATE KEY-----"
    paths = {"rules": ".claude/rules/public.md", "memories": "memory/MEMORY.md", "skills": ".claude/skills/public/SKILL.md", "instructions": "CLAUDE.md"}
    path = project / paths[kind]; path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("name: example\ndescription: " + key)
    if kind == "memories":
        (project / ".claude/settings.json").write_text(json.dumps({"autoMemoryDirectory": str(project / "memory")}))
    context = capture(project, home=home)
    assert "SYNTHETIC PRIVATE KEY BODY" not in json.dumps(context)
    assert any("private-key content" in warning for warning in context["warnings"])


def test_project_can_import_curated_user_rules_but_not_user_auth_files(tmp_path):
    home = tmp_path / "home"; project = tmp_path / "repo"
    (home / ".claude/rules").mkdir(parents=True); project.mkdir()
    (home / ".claude/rules/public.md").write_text("User-authored shared rule")
    (home / ".claude/rules/.credentials.json").write_text("PRIVATE AUTH VALUE")
    (project / "CLAUDE.md").write_text("@~/.claude/rules/public.md\n@~/.claude/rules/.credentials.json")
    context = capture(project, home=home)
    assert "User-authored shared rule" in render(context)
    assert "PRIVATE AUTH VALUE" not in json.dumps(context)



def test_ancestor_rule_imports_stay_within_selected_rule_directory(tmp_path):
    home = tmp_path / "home"; project = tmp_path / "repo"
    home.mkdir(); (project / "nested").mkdir(parents=True)
    rules = project / ".claude/rules"; (rules / "details").mkdir(parents=True)
    (rules / "api.md").write_text("@details/contract.md")
    (rules / "details/contract.md").write_text("Ancestor rule import")
    context = capture(project / "nested", home=home)
    assert "Ancestor rule import" in render(context)


def test_symlinked_user_instruction_does_not_elevate_project_imports(tmp_path, symlink):
    home = tmp_path / "home"; project = tmp_path / "repo"
    home.mkdir(); project.mkdir()
    (home / "private.md").write_text("UNRELATED USER SECRET")
    (project / "CLAUDE.md").write_text("@~/private.md")
    symlink(home / "CLAUDE.md", project / "CLAUDE.md")
    assert "UNRELATED USER SECRET" not in json.dumps(capture(project, home=home))


def test_project_scope_is_working_tree_not_shared_main_repository(tmp_path):
    home = tmp_path / "home"; project = tmp_path / "main"
    home.mkdir(); project.mkdir()
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    subprocess.run(["git", "-C", str(project), "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "--allow-empty", "-qm", "initial"], check=True)
    worktree = tmp_path / "worktree"
    subprocess.run(["git", "-C", str(project), "worktree", "add", "-qb", "test", str(worktree)], check=True)
    (project / "private.md").write_text("MAIN CHECKOUT PRIVATE FILE")
    (worktree / "CLAUDE.md").write_text(f"@{project}/private.md\n@nested/rules.md")
    (worktree / "nested").mkdir(); (worktree / "nested/rules.md").write_text("Worktree nested import")
    context = capture(worktree / "nested", home=home)
    assert "Worktree nested import" in render(context)
    assert "MAIN CHECKOUT PRIVATE FILE" not in json.dumps(context)


def test_rejected_external_import_is_never_opened(tmp_path, monkeypatch):
    from pathlib import Path
    home = tmp_path / "home"; project = tmp_path / "repo"
    home.mkdir(); project.mkdir()
    secret = home / "private.md"; secret.write_text("DO NOT READ")
    (project / "CLAUDE.md").write_text(f"@{secret}")
    original = Path.read_text
    def checked_read(path, *args, **kwargs):
        assert path != secret, "Disallowed context paths must be checked before opening"
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", checked_read)
    assert capture(project, home=home)["warnings"]



def test_plugin_skill_paths_come_only_from_user_installed_manifest(tmp_path):
    home = tmp_path / "home"; project = tmp_path / "repo"
    user_plugins = home / ".claude/plugins"; user_plugins.mkdir(parents=True)
    repo_plugins = project / ".claude/plugins"; repo_plugins.mkdir(parents=True)
    trusted = tmp_path / "trusted-plugin/skills/review"; trusted.mkdir(parents=True)
    untrusted = tmp_path / "untrusted-plugin/skills/private"; untrusted.mkdir(parents=True)
    (trusted / "SKILL.md").write_text("name: review\ndescription: User-installed review plugin")
    (untrusted / "SKILL.md").write_text("name: leaked\ndescription: UNTRUSTED PLUGIN CONTENT")
    (user_plugins / "installed_plugins.json").write_text(json.dumps({"plugins": {"review": [{"installPath": str(trusted.parent.parent)}]}}))
    (repo_plugins / "installed_plugins.json").write_text(json.dumps({"plugins": {"private": [{"installPath": str(untrusted.parent.parent)}]}}))
    (project / ".claude/settings.json").write_text(json.dumps({"enabledPlugins": {"review": True, "private": True}}))
    context = capture(project, home=home)
    assert any(skill["name"] == "review" for skill in context["skills"])
    assert "UNTRUSTED PLUGIN CONTENT" not in json.dumps(context)


def test_trusted_user_import_still_cannot_read_credentials(tmp_path):
    home = tmp_path / "home"; project = tmp_path / "repo"
    (home / ".ssh").mkdir(parents=True); project.mkdir()
    (home / ".ssh/notes.md").write_text("PRIVATE CREDENTIAL DIRECTORY CONTENT")
    (home / "CLAUDE.md").write_text("@~/.ssh/notes.md")
    context = capture(project, home=home)
    assert "PRIVATE CREDENTIAL DIRECTORY CONTENT" not in json.dumps(context)
    assert any("sensitive" in warning for warning in context["warnings"])



def test_user_document_grants_do_not_follow_imports_into_project(tmp_path):
    home = tmp_path / "home"; project = tmp_path / "repo"
    home.mkdir(); project.mkdir()
    (home / "CLAUDE.md").write_text("@~/preferences.md")
    (home / "preferences.md").write_text(f"@{project}/CLAUDE.md")
    (home / "private.md").write_text("UNRELATED TRANSITIVE SECRET")
    (project / "CLAUDE.md").write_text("@~/private.md")
    assert "UNRELATED TRANSITIVE SECRET" not in json.dumps(capture(project, home=home))


def test_user_import_of_ancestor_project_rules_does_not_elevate_them(tmp_path):
    home = tmp_path / "home"; project = tmp_path / "repo"
    home.mkdir(); (project / "nested").mkdir(parents=True)
    (home / "CLAUDE.md").write_text(f"@{project}/CLAUDE.md")
    (home / "private.md").write_text("UNRELATED ANCESTOR SECRET")
    (project / "CLAUDE.md").write_text("@~/private.md")
    assert "UNRELATED ANCESTOR SECRET" not in json.dumps(capture(project / "nested", home=home))



def test_default_memory_capture_does_not_read_repository_memory(tmp_path):
    home = tmp_path / "home"; project = tmp_path / "repo"
    home.mkdir(); (project / "memory").mkdir(parents=True)
    (project / "memory/MEMORY.md").write_text("UNSELECTED REPOSITORY MEMORY")
    context = capture(project, home=home)
    assert "UNSELECTED REPOSITORY MEMORY" not in json.dumps(context)
    assert str(project / "memory") not in context["memory_roots"]


@pytest.mark.skipif(sys.platform == "win32", reason="A Windows path is a literal fixture filename on POSIX only")
def test_legacy_memory_slug_cannot_contain_windows_drive_syntax(tmp_path, monkeypatch):
    from pathlib import PureWindowsPath
    home = tmp_path / "home"; project = tmp_path / "repo"
    project.mkdir()
    repository = PureWindowsPath(r"D:\work\main-repo")
    monkeypatch.setattr("codex_sessions.context._repository_root", lambda _: repository)
    projects = home / ".claude/projects"
    canonical = projects / re.sub(r"[^a-zA-Z0-9]", "-", str(repository)) / "memory"
    canonical.mkdir(parents=True)
    (canonical / "MEMORY.md").write_text("Canonical project memory")
    legacy = projects / str(repository) / "memory"
    legacy.mkdir(parents=True)
    (legacy / "MEMORY.md").write_text("UNSAFE LEGACY PATH MEMORY")
    context = capture(project, home=home)
    assert "Canonical project memory" in render(context)
    assert "UNSAFE LEGACY PATH MEMORY" not in json.dumps(context)
    assert context["memory_roots"] == [str(canonical)]



def test_explicit_home_bounds_ancestor_discovery_to_selected_context(tmp_path):
    host = tmp_path / "host-user"
    isolated = host / "isolated"
    home = isolated / "home"; project = isolated / "repo"; launch = project / "nested"
    (home / ".claude").mkdir(parents=True); launch.mkdir(parents=True)
    (host / ".claude/rules").mkdir(parents=True)
    (host / ".claude/skills/host-only").mkdir(parents=True)
    (host / "private-memory").mkdir()
    (host / "CLAUDE.md").write_text("UNSELECTED HOST INSTRUCTIONS")
    (host / ".claude/rules/private.md").write_text("UNSELECTED HOST RULE")
    (host / ".claude/skills/host-only/SKILL.md").write_text("name: host-only\ndescription: UNSELECTED HOST SKILL")
    (host / "private-memory/MEMORY.md").write_text("UNSELECTED HOST MEMORY")
    (host / ".claude/settings.json").write_text(json.dumps({"model": "host-model", "enabledPlugins": {"host-only": True}, "autoMemoryDirectory": str(host / "private-memory")}))
    (isolated / "CLAUDE.md").write_text("Selected common ancestor instruction")
    (home / "CLAUDE.md").write_text("Selected home instruction")
    (home / ".claude/settings.json").write_text(json.dumps({"model": "selected-model"}))
    (project / "CLAUDE.md").write_text("Selected project instruction")
    context = capture(launch, home=home)
    body = json.dumps(context)
    assert all(text in body for text in ("Selected common ancestor instruction", "Selected home instruction", "Selected project instruction"))
    assert "UNSELECTED HOST" not in body and "host-only" not in body
    assert context["settings"]["model"] == "selected-model"
    assert context["settings_sources"] == [str(home / ".claude/settings.json")]



def test_explicit_home_without_common_drive_uses_working_tree_boundary(tmp_path, monkeypatch):
    host = tmp_path / "host-user"; home = tmp_path / "selected-home"
    project = host / "repo"; launch = project / "nested"
    home.mkdir(); launch.mkdir(parents=True)
    (host / "CLAUDE.md").write_text("UNSELECTED HOST ANCESTOR")
    (home / "CLAUDE.md").write_text("Selected separate home instruction")
    (project / "CLAUDE.md").write_text("Selected working tree instruction")
    def different_drives(_paths):
        raise ValueError("Paths are on different drives")
    monkeypatch.setattr("codex_sessions.context.os.path.commonpath", different_drives)
    monkeypatch.setattr("codex_sessions.context._working_tree_root", lambda _: project)
    context = capture(launch, home=home)
    body = json.dumps(context)
    assert "Selected separate home instruction" in body and "Selected working tree instruction" in body
    assert "UNSELECTED HOST ANCESTOR" not in body



@pytest.fixture
def deny_network_path_io(monkeypatch):
    from pathlib import Path
    for name in ("resolve", "exists", "is_file", "is_dir", "stat", "lstat", "read_text"):
        original = getattr(Path, name)
        def guarded(path, *args, _method=original, **kwargs):
            normalized = str(path).replace("\\", "/")
            assert not any(prefix in normalized for prefix in ("//server", "//?/", "//./")), "Network paths must be rejected before filesystem access"
            return _method(path, *args, **kwargs)
        monkeypatch.setattr(Path, name, guarded)


@pytest.mark.parametrize("value", [r"\\server\share\rules.md", "//server/share/rules.md", r"\\?\UNC\server\share\rules.md", r"\\.\pipe\context"])
@pytest.mark.parametrize("source", ["project", "user"])
def test_context_import_rejects_network_paths_before_any_io(tmp_path, value, source, deny_network_path_io):
    home = tmp_path / "home"; project = tmp_path / "repo"
    home.mkdir(); project.mkdir()
    ((project if source == "project" else home) / "CLAUDE.md").write_text("@" + value)
    context = capture(project, home=home)
    assert any("network and device paths" in warning for warning in context["warnings"])


@pytest.mark.parametrize("value", [r"\\server\share\memory", "//server/share/memory", r"\\?\C:\private\memory"])
def test_memory_override_rejects_network_paths_before_any_io(tmp_path, value, deny_network_path_io):
    home = tmp_path / "home"; project = tmp_path / "repo"
    home.mkdir(); (project / ".claude").mkdir(parents=True)
    (project / ".claude/settings.json").write_text(json.dumps({"autoMemoryDirectory": value}))
    context = capture(project, home=home)
    assert any("network and device paths" in warning for warning in context["warnings"])
    assert not context["memory_roots"]


@pytest.mark.parametrize("kind", ["instructions", "rules", "skills", "settings", "memories"])
def test_windows_project_reparse_component_is_rejected_before_following(tmp_path, monkeypatch, kind):
    from pathlib import Path
    from types import SimpleNamespace
    from codex_sessions import context as context_module
    home = tmp_path / "home"; project = tmp_path / "repo"
    home.mkdir(); (project / ".claude").mkdir(parents=True)
    targets = {"instructions": "linked", "rules": ".claude/rules", "skills": ".claude/skills/example", "settings": ".claude/settings.json", "memories": "memory"}
    link = project / targets[kind]
    if kind == "settings":
        link.write_text(json.dumps({"model": "REPARSE TARGET CONTENT"}))
    else:
        link.mkdir(parents=True)
        filename = "SKILL.md" if kind == "skills" else "rules.md"
        (link / filename).write_text("name: example\ndescription: REPARSE TARGET CONTENT")
    if kind == "instructions":
        (project / "CLAUDE.md").write_text("@linked/rules.md")
    if kind == "memories":
        (project / ".claude/settings.json").write_text(json.dumps({"autoMemoryDirectory": str(link)}))
    monkeypatch.setattr(context_module, "sys", SimpleNamespace(platform="win32"), raising=False)
    original_lstat = Path.lstat
    monkeypatch.setattr(Path, "lstat", lambda path, *args, **kwargs: SimpleNamespace(st_file_attributes=0x400) if path == link else original_lstat(path, *args, **kwargs))
    for name in ("resolve", "exists", "is_file", "is_dir", "stat", "read_text"):
        original = getattr(Path, name)
        def guarded(path, *args, _method=original, **kwargs):
            assert not path.is_relative_to(link), "Reparse components must be rejected before following them"
            return _method(path, *args, **kwargs)
        monkeypatch.setattr(Path, name, guarded)
    context = capture(project, home=home)
    assert "REPARSE TARGET CONTENT" not in json.dumps(context)
    assert any("reparse point" in warning for warning in context["warnings"])



@pytest.mark.parametrize("kind", ["instructions", "memories"])
def test_untrusted_external_context_paths_are_rejected_before_metadata(tmp_path, monkeypatch, kind):
    from pathlib import Path
    home = tmp_path / "home"; project = tmp_path / "repo"; external = tmp_path / "outside" / "target"
    home.mkdir(); (project / ".claude").mkdir(parents=True)
    if kind == "instructions":
        (project / "CLAUDE.md").write_text("@" + str(external))
    else:
        (project / ".claude/settings.json").write_text(json.dumps({"autoMemoryDirectory": str(external)}))
    for name in ("resolve", "exists", "is_file", "is_dir", "stat", "lstat", "read_text"):
        original = getattr(Path, name)
        def guarded(path, *args, _method=original, **kwargs):
            assert path != external, "External project context must be rejected before filesystem access"
            return _method(path, *args, **kwargs)
        monkeypatch.setattr(Path, name, guarded)
    context = capture(project, home=home)
    assert any("outside allowed context roots" in warning for warning in context["warnings"])
