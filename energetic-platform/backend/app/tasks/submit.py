from __future__ import annotations

import time
from pathlib import Path

from app.cluster.slurm import SlurmClient
from app.cluster.ssh_client import ClusterSSHClient
from app.config import load_cluster_config
from app.generators.orca_input import render_orca_opt_input, render_orca_slurm_script


LOCAL_RUNTIME_ROOT = Path("/app/runtime")


def submit_orca_optimization(request) -> dict[str, str]:
    config = load_cluster_config()
    cluster = config["cluster"]
    slurm_cfg = config["slurm"]
    orca_cfg = config["orca"]
    orca_slurm = slurm_cfg["orca"]

    workflow_id = f"{request.molecule_name}_{int(time.time())}"
    local_dir = LOCAL_RUNTIME_ROOT / workflow_id / "01_orca_opt"
    local_dir.mkdir(parents=True, exist_ok=True)

    nprocs = int(orca_slurm["ntasks"])
    opt_input = render_orca_opt_input(
        xyz=request.xyz,
        charge=request.charge,
        multiplicity=request.multiplicity,
        nprocs=nprocs,
        maxcore_mb=3000,
    )
    (local_dir / "opt.inp").write_text(opt_input, encoding="utf-8")

    script = render_orca_slurm_script(
        {
            "job_name": "orca_opt",
            "partition": slurm_cfg["partition"],
            "account": slurm_cfg.get("account"),
            "qos": slurm_cfg.get("qos"),
            "nodes": orca_slurm["nodes"],
            "ntasks": nprocs,
            "memory": orca_slurm["memory"],
            "walltime": orca_slurm["walltime"],
            "orca_env_script": orca_cfg["environment_script"],
            "orca_executable": orca_cfg["executable"],
            "scratch_root": cluster["remote_scratch_root"],
        }
    )
    (local_dir / "job.sh").write_text(script, encoding="utf-8")

    remote_dir = f"{cluster['remote_work_root']}/{workflow_id}/01_orca_opt"
    ssh = ClusterSSHClient(
        host=cluster["host"],
        port=int(cluster.get("port", 22)),
        username=cluster["username"],
        private_key_path=cluster["private_key"],
    )

    ssh.put_directory(local_dir, remote_dir)
    slurm = SlurmClient(ssh)
    job_id = slurm.submit(remote_dir, "job.sh")

    return {
        "workflow_id": workflow_id,
        "remote_directory": remote_dir,
        "local_package": str(local_dir),
        "slurm_job_id": job_id,
        "next_step": "Monitor this Slurm job id and parse opt.out after completion.",
    }
