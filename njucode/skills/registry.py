"""Skill Registry - Manages skill registration, lifecycle, and discovery.

This module provides:
- Skill registration (builtin and plugins)
- Skill lookup and command mapping
- Enable/disable management
- Persistence to .nju_code/settings.json
"""

from __future__ import annotations

import importlib.util
import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import (
    SkillKind,
    SkillManifest,
    SkillStatus,
    SkillToggle,
    SkillExecutionLog,
)
from .audit_log import AuditLogger


class SkillRegistry:
    """Central registry for skill management.

    Handles:
    1. Builtin skill registration
    2. External plugin loading
    3. Skill state persistence
    4. Command-to-skill mapping
    """

    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root
        self.skills: Dict[str, SkillToggle] = {}
        self.builtin_skills: Dict[str, SkillManifest] = {}
        self.plugin_skills: Dict[str, SkillManifest] = {}
        self.agent_skills: Dict[str, SkillManifest] = {}
        self._audit_logger: Optional[AuditLogger] = None
        self._settings_path = workspace_root / ".nju_code" / "settings.json"

    def set_audit_logger(self, logger: AuditLogger) -> None:
        """Set audit logger reference."""
        self._audit_logger = logger

    def register_skill(self, manifest: SkillManifest) -> SkillToggle:
        """Register a skill with its manifest.

        Args:
            manifest: Skill manifest defining capabilities

        Returns:
            SkillToggle with default enabled state
        """
        toggle = SkillToggle(
            skill_id=manifest.skill_id,
            manifest=manifest,
            enabled=True,
            status=SkillStatus.ENABLED,
        )

        if manifest.kind == SkillKind.AGENT:
            self.agent_skills[manifest.skill_id] = manifest
        elif manifest.is_builtin:
            self.builtin_skills[manifest.skill_id] = manifest
        else:
            self.plugin_skills[manifest.skill_id] = manifest

        self.skills[manifest.skill_id] = toggle
        return toggle

    def unregister_skill(self, skill_id: str) -> bool:
        """Remove a skill from registry.

        Args:
            skill_id: Skill to remove

        Returns:
            True if skill was removed, False if not found
        """
        if skill_id not in self.skills:
            return False

        toggle = self.skills.pop(skill_id)
        if toggle.manifest.kind == SkillKind.AGENT:
            self.agent_skills.pop(skill_id, None)
        elif toggle.manifest.is_builtin:
            self.builtin_skills.pop(skill_id, None)
        else:
            self.plugin_skills.pop(skill_id, None)

        return True

    def get_skill(self, skill_id: str) -> Optional[SkillToggle]:
        """Get skill toggle by ID."""
        return self.skills.get(skill_id)

    def get_manifest(self, skill_id: str) -> Optional[SkillManifest]:
        """Get skill manifest by ID."""
        toggle = self.skills.get(skill_id)
        return toggle.manifest if toggle else None

    def get_skill_by_command(self, command: str) -> Optional[SkillManifest]:
        """Find skill by command alias.

        Args:
            command: Command string like "/scan", "/search keyword"

        Returns:
            Matching SkillManifest or None
        """
        # Normalize command
        cmd = command.strip().lower()
        if not cmd.startswith("/"):
            return None

        # Extract base command
        base_cmd = cmd.split()[0] if " " in cmd else cmd

        for manifest in list(self.builtin_skills.values()) + list(self.plugin_skills.values()):
            if base_cmd in [alias.lower() for alias in manifest.command_aliases]:
                return manifest

        return None

    def update_skill_status(self, skill_id: str, enabled: bool) -> None:
        """Update skill enabled status."""
        if skill_id in self.skills:
            self.skills[skill_id].enabled = enabled
            self.skills[skill_id].status = (
                SkillStatus.ENABLED if enabled else SkillStatus.DISABLED
            )

    def increment_usage(self, skill_id: str) -> None:
        """Increment skill usage counter."""
        if skill_id in self.skills:
            self.skills[skill_id].usage_count += 1

    def list_skills(
        self,
        category: Optional[str] = None,
        enabled_only: bool = False
    ) -> List[SkillToggle]:
        """List skills with optional filters.

        Args:
            category: Filter by category (analysis, retrieval, etc.)
            enabled_only: Only return enabled skills

        Returns:
            List of SkillToggle objects
        """
        result = []
        for toggle in self.skills.values():
            if category and toggle.manifest.category != category:
                continue
            if enabled_only and not toggle.enabled:
                continue
            result.append(toggle)

        return sorted(result, key=lambda t: t.manifest.name)

    def check_dependencies(self, skill_id: str) -> List[str]:
        """Check if skill dependencies are satisfied.

        Returns:
            List of missing dependency skill IDs
        """
        manifest = self.get_manifest(skill_id)
        if not manifest:
            return []

        missing = []
        for dep_id in manifest.dependencies:
            if dep_id not in self.skills or not self.skills[dep_id].enabled:
                missing.append(dep_id)

        return missing

    def load_plugins(self) -> List[str]:
        """Load external plugins from .nju_code/plugins/.

        Returns:
            List of loaded plugin skill IDs
        """
        plugins_dir = self.workspace_root / ".nju_code" / "plugins"
        if not plugins_dir.exists():
            return []

        loaded = []
        for plugin_dir in plugins_dir.iterdir():
            if not plugin_dir.is_dir():
                continue

            manifest_path = plugin_dir / "manifest.json"
            if not manifest_path.exists():
                continue

            try:
                manifest = self._load_plugin_manifest(manifest_path, plugin_dir)
                self.register_skill(manifest)
                loaded.append(manifest.skill_id)
            except Exception as e:
                # Log error but don't crash
                print(f"[Skills] Failed to load plugin {plugin_dir.name}: {e}")

        return loaded

    def load_agent_skills(self) -> List[str]:
        """Load Codex-style agent skills from .nju_code/skills/*/SKILL.md.

        Agent skills are not direct commands. They are instruction bundles that
        can be selected for a user request and injected into model context.
        """
        skills_dir = self.workspace_root / ".nju_code" / "skills"
        if not skills_dir.exists():
            return []

        loaded: list[str] = []
        for skill_dir in skills_dir.iterdir():
            if not skill_dir.is_dir():
                continue
            skill_file = skill_dir / "SKILL.md"
            if not skill_file.exists():
                continue
            try:
                manifest = self._load_agent_skill(skill_file, skill_dir)
                self.register_skill(manifest)
                loaded.append(manifest.skill_id)
            except Exception as e:
                print(f"[Skills] Failed to load agent skill {skill_dir.name}: {e}")
        return loaded

    def _load_agent_skill(self, skill_file: Path, skill_dir: Path) -> SkillManifest:
        frontmatter, body = self._parse_skill_markdown(skill_file)
        name = frontmatter.get("name") or skill_dir.name
        description = frontmatter.get("description", "")
        skill_id = f"agent.{self._normalize_skill_name(name)}"

        return SkillManifest(
            skill_id=skill_id,
            name=name,
            version="1.0.0",
            description=description,
            category="agent",
            permissions=[],
            command_aliases=[],
            author=frontmatter.get("author", "Unknown"),
            is_builtin=False,
            plugin_path=str(skill_dir),
            entry_point=None,
            kind=SkillKind.AGENT,
            instructions_path=str(skill_file),
            instructions=body.strip(),
        )

    def _parse_skill_markdown(self, skill_file: Path) -> tuple[dict[str, str], str]:
        text = skill_file.read_text(encoding="utf-8", errors="ignore")
        if not text.startswith("---"):
            return {}, text

        match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, flags=re.DOTALL)
        if not match:
            return {}, text

        raw_frontmatter, body = match.groups()
        frontmatter: dict[str, str] = {}
        for line in raw_frontmatter.splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            frontmatter[key.strip()] = value.strip().strip("\"'")
        return frontmatter, body

    def _normalize_skill_name(self, name: str) -> str:
        normalized = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        return normalized or "unnamed-skill"

    def select_agent_skills(self, query: str, max_skills: int = 3) -> List[SkillToggle]:
        """Select enabled agent skills relevant to a request.

        This intentionally stays lightweight: metadata is used for selection,
        while full SKILL.md bodies are only loaded after a skill is selected.
        """
        query_tokens = {
            token
            for token in re.findall(r"[a-zA-Z0-9_\-]+", query.lower())
            if len(token) >= 3
        }
        explicit_refs = {
            token.lower().replace("_", "-")
            for token in re.findall(r"\$([A-Za-z0-9_\-]+)", query)
        }

        scored: list[tuple[float, str, SkillToggle]] = []
        for toggle in self.skills.values():
            manifest = toggle.manifest
            if manifest.kind != SkillKind.AGENT or not toggle.enabled:
                continue
            haystack = f"{manifest.name} {manifest.description}".lower()
            hay_tokens = set(re.findall(r"[a-zA-Z0-9_\-]+", haystack))
            score = float(len(query_tokens & hay_tokens))
            normalized_name = self._normalize_skill_name(manifest.name)
            if normalized_name in explicit_refs:
                score += 100.0
            if score > 0:
                scored.append((score, manifest.name, toggle))

        scored.sort(key=lambda item: (-item[0], item[1]))
        return [toggle for _, _, toggle in scored[:max_skills]]

    def _load_plugin_manifest(
        self,
        manifest_path: Path,
        plugin_dir: Path
    ) -> SkillManifest:
        """Parse plugin manifest.json into SkillManifest."""
        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Convert permission strings to enum
        from .models import SkillPermissionLevel, SkillParameter, SkillOutput

        permissions = []
        for perm_str in data.get("permissions", ["read_only"]):
            try:
                permissions.append(SkillPermissionLevel(perm_str))
            except ValueError:
                permissions.append(SkillPermissionLevel.READ_ONLY)

        # Parse parameters
        parameters = []
        for param_data in data.get("parameters", []):
            parameters.append(SkillParameter(
                name=param_data.get("name", ""),
                type=param_data.get("type", "string"),
                required=param_data.get("required", True),
                default=param_data.get("default"),
                description=param_data.get("description", ""),
                validation_pattern=param_data.get("validation_pattern"),
            ))

        # Parse output
        output = None
        if "output" in data:
            output = SkillOutput(
                type=data["output"].get("type", "text"),
                schema=data["output"].get("schema"),
                description=data["output"].get("description", ""),
            )

        return SkillManifest(
            skill_id=data.get("skill_id", f"plugin.{plugin_dir.name}"),
            name=data.get("name", plugin_dir.name),
            version=data.get("version", "1.0.0"),
            description=data.get("description", ""),
            category=data.get("category", "utility"),
            parameters=parameters,
            output=output,
            permissions=permissions,
            dependencies=data.get("dependencies", []),
            command_aliases=data.get("command_aliases", []),
            author=data.get("author", "Unknown"),
            homepage=data.get("homepage"),
            is_builtin=False,
            plugin_path=str(plugin_dir),
            entry_point=data.get("entry_point", "main.py"),
            kind=SkillKind.PLUGIN,
        )

    def save(self) -> None:
        """Persist skill states to settings.json."""
        # Load existing settings
        settings: Dict[str, Any] = {}
        if self._settings_path.exists():
            try:
                with open(self._settings_path, "r", encoding="utf-8") as f:
                    settings = json.load(f)
            except Exception:
                pass

        # Add skills section
        settings["skills"] = {
            skill_id: {
                "enabled": toggle.enabled,
                "usage_count": toggle.usage_count,
                "status": toggle.status.value,
            }
            for skill_id, toggle in self.skills.items()
        }

        # Save
        try:
            self._settings_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._settings_path, "w", encoding="utf-8") as f:
                json.dump(settings, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[Skills] Failed to save settings: {e}")

    def load(self) -> None:
        """Load skill states from settings.json."""
        if not self._settings_path.exists():
            return

        try:
            with open(self._settings_path, "r", encoding="utf-8") as f:
                settings = json.load(f)
        except Exception:
            return

        skills_data = settings.get("skills", {})
        for skill_id, data in skills_data.items():
            if skill_id in self.skills:
                self.skills[skill_id].enabled = data.get("enabled", True)
                self.skills[skill_id].usage_count = data.get("usage_count", 0)
                try:
                    self.skills[skill_id].status = SkillStatus(data.get("status", "enabled"))
                except ValueError:
                    self.skills[skill_id].status = SkillStatus.ENABLED
