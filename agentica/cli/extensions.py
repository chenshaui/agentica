# -*- coding: utf-8 -*-
"""
@author:XuMing(xuming624@qq.com)
@description: CLI handlers for extension installation.
"""
from agentica.cli.config import console
from agentica.config import AGENTICA_SKILL_DIR
from agentica.skills import get_skill_registry, load_skills, reset_skill_registry
from agentica.skills.installer import install_skills, list_installed_skills, remove_skill


def run_extensions_command(args) -> None:
    """Execute `agentica extensions ...` subcommands."""
    target_dir = args.target_dir or AGENTICA_SKILL_DIR

    if args.extensions_command == "install":
        replaced_symlinked_skills: list[str] = []
        installed = install_skills(
            args.source,
            destination_dir=target_dir,
            force=args.force,
            replaced_symlinked_skills=replaced_symlinked_skills,
        )
        console.print(
            f"[green]Installed {len(installed)} skill(s) into {target_dir}[/green]"
        )
        for skill in installed:
            console.print(f"  - [bold]{skill.name}[/bold]: {skill.description}")
        for skill_name in replaced_symlinked_skills:
            console.print(f"[green]replaced existing symlinked skill: {skill_name}[/green]")
        if args.target_dir:
            console.print(
                "[yellow]Note: custom --target-dir is only auto-discovered when it is a standard skills path or included in AGENTICA_EXTRA_SKILL_PATH.[/yellow]"
            )
        return

    if args.extensions_command == "list":
        skills = list_installed_skills(destination_dir=target_dir)
        if not skills:
            console.print(f"[yellow]No skills installed in {target_dir}[/yellow]")
            return
        console.print(f"[green]Installed skills in {target_dir}[/green]")
        for skill in skills:
            console.print(f"  - [bold]{skill.name}[/bold]: {skill.description}")
        return

    if args.extensions_command == "remove":
        removed_path = remove_skill(args.skill_name, destination_dir=target_dir)
        console.print(
            f"[green]Removed skill {args.skill_name} from {removed_path}[/green]"
        )
        return

    if args.extensions_command == "reload":
        reset_skill_registry()
        load_skills()
        registry = get_skill_registry()
        console.print(
            f"[green]Reloaded {len(registry)} skill(s) from standard search paths[/green]"
        )
        return

    raise ValueError(f"Unsupported extensions command: {args.extensions_command}")
