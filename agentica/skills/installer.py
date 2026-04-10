# -*- coding: utf-8 -*-
"""
@author:XuMing(xuming624@qq.com)
@description: Install external skill repositories into the local Agentica skill directory.
"""
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple

from agentica.config import AGENTICA_SKILL_DIR
from agentica.skills.skill import Skill
from agentica.skills.skill_loader import SkillLoader
from agentica.utils.log import logger


def _remove_installed_skill_path(path: Path) -> None:
    """Remove an installed skill path, handling both real directories and symlinks."""
    if path.is_symlink() or path.is_file():
        path.unlink()
        return
    if path.exists():
        shutil.rmtree(path)


def _resolve_skill_source(source: str) -> Tuple[Path, Optional[tempfile.TemporaryDirectory]]:
    """Resolve a local directory or clone a remote git repository."""
    source_path = Path(source).expanduser()
    if source_path.exists():
        return source_path.resolve(), None

    temp_dir = tempfile.TemporaryDirectory(prefix="agentica-skill-install-")
    clone_dir = Path(temp_dir.name) / "repo"
    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", source, str(clone_dir)],
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        temp_dir.cleanup()
        raise
    return clone_dir, temp_dir


def _discover_installable_skill_files(source_root: Path) -> List[Path]:
    """Discover installable skills from a single-skill repo or a skill collection repo."""
    loader = SkillLoader(project_root=source_root)
    candidate_roots = []
    if (source_root / "SKILL.md").is_file():
        candidate_roots.append(source_root)
    for relative_dir in ("skills", ".agentica/skills", ".claude/skills"):
        candidate_root = source_root / relative_dir
        if candidate_root.is_dir():
            candidate_roots.append(candidate_root)

    skill_files: List[Path] = []
    seen: set[Path] = set()
    for candidate_root in candidate_roots:
        if candidate_root == source_root:
            discovered = [candidate_root / "SKILL.md"]
        else:
            discovered = loader.discover_skills(candidate_root)
        for skill_file in discovered:
            resolved = skill_file.resolve()
            if resolved not in seen:
                seen.add(resolved)
                skill_files.append(resolved)
    return skill_files


def install_skills(
    source: str,
    destination_dir: Optional[str] = None,
    force: bool = False,
    replaced_symlinked_skills: Optional[List[str]] = None,
) -> List[Skill]:
    """Install skills from a git URL or local directory into the Agentica user skill dir."""
    install_root = Path(destination_dir or AGENTICA_SKILL_DIR).expanduser().resolve()
    install_root.mkdir(parents=True, exist_ok=True)

    source_root, temp_dir = _resolve_skill_source(source)
    try:
        skill_files = _discover_installable_skill_files(source_root)
        if not skill_files:
            raise ValueError(f"No installable skills found in '{source}'.")

        installed: List[Skill] = []
        for skill_file in skill_files:
            skill = Skill.from_skill_md(skill_file, location="managed")
            if skill is None:
                continue

            target_dir = install_root / skill.name
            if target_dir.exists():
                if not force:
                    raise FileExistsError(
                        f"Skill '{skill.name}' already exists at '{target_dir}'. "
                        "Pass force=True programmatically or use --force in the CLI to replace it."
                    )
                if target_dir.is_symlink() and replaced_symlinked_skills is not None:
                    replaced_symlinked_skills.append(skill.name)
                _remove_installed_skill_path(target_dir)

            shutil.copytree(skill.path, target_dir)
            installed_skill = Skill.from_skill_md(target_dir / "SKILL.md", location="user")
            if installed_skill is not None:
                installed.append(installed_skill)
                logger.info(f"Installed skill: {installed_skill.name} -> {target_dir}")

        if not installed:
            raise ValueError(f"No valid skills could be installed from '{source}'.")

        return installed
    finally:
        if temp_dir is not None:
            temp_dir.cleanup()


def list_installed_skills(destination_dir: Optional[str] = None) -> List[Skill]:
    """List installed skills from the Agentica user skill directory."""
    install_root = Path(destination_dir or AGENTICA_SKILL_DIR).expanduser().resolve()
    if not install_root.exists():
        return []

    loader = SkillLoader(project_root=install_root)
    skills: List[Skill] = []
    for skill_file in loader.discover_skills(install_root):
        skill = Skill.from_skill_md(skill_file, location="user")
        if skill is not None:
            skills.append(skill)
    skills.sort(key=lambda skill: skill.name)
    return skills


def remove_skill(skill_name: str, destination_dir: Optional[str] = None) -> Path:
    """Remove an installed skill directory by its skill name."""
    install_root = Path(destination_dir or AGENTICA_SKILL_DIR).expanduser().resolve()
    target_dir = install_root / skill_name
    if not target_dir.exists():
        raise FileNotFoundError(f"Skill '{skill_name}' is not installed in '{install_root}'.")
    _remove_installed_skill_path(target_dir)
    logger.info(f"Removed installed skill: {skill_name} from {target_dir}")
    return target_dir
