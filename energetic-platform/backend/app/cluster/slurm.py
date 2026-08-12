from __future__ import annotations

import shlex

from app.cluster.ssh_client import ClusterSSHClient


class SlurmClient:
    def __init__(self, ssh_client: ClusterSSHClient) -> None:
        self.ssh_client = ssh_client

    def submit(self, remote_directory: str, script_name: str) -> str:
        command = (
            f"cd {shlex.quote(remote_directory)} && "
            f"sbatch --parsable {shlex.quote(script_name)}"
        )
        code, stdout, stderr = self.ssh_client.execute(command)
        if code != 0:
            raise RuntimeError(f"Slurm submission failed: {stderr}")
        return stdout.strip().split(";")[0]

    def status(self, job_id: str) -> str:
        command = (
            f"sacct -n -X -j {shlex.quote(job_id)} "
            "--format=State --parsable2"
        )
        code, stdout, stderr = self.ssh_client.execute(command)
        if code != 0:
            raise RuntimeError(f"Slurm query failed: {stderr}")
        states = [line.split("|")[0].strip() for line in stdout.splitlines() if line.strip()]
        return states[0] if states else "UNKNOWN"
