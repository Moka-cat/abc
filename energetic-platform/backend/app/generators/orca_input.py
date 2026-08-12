from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader


TEMPLATE_ROOT = Path(__file__).resolve().parents[2] / "templates"


def render_orca_opt_input(
    xyz: str,
    charge: int,
    multiplicity: int,
    nprocs: int,
    maxcore_mb: int,
) -> str:
    env = Environment(loader=FileSystemLoader(TEMPLATE_ROOT / "orca"))
    template = env.get_template("opt.inp.j2")
    return template.render(
        xyz=xyz.strip(),
        charge=charge,
        multiplicity=multiplicity,
        nprocs=nprocs,
        maxcore_mb=maxcore_mb,
    )


def render_orca_slurm_script(context: dict[str, object]) -> str:
    env = Environment(loader=FileSystemLoader(TEMPLATE_ROOT / "slurm"))
    template = env.get_template("orca.sh.j2")
    return template.render(**context)
