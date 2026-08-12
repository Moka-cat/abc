from __future__ import annotations

from pydantic import BaseModel, Field
from fastapi import APIRouter

from app.cluster.slurm import SlurmClient
from app.cluster.ssh_client import ClusterSSHClient
from app.config import load_cluster_config
from app.tasks.submit import submit_orca_optimization

router = APIRouter()


class OrcaOptimizationRequest(BaseModel):
    molecule_name: str = Field(min_length=1)
    xyz: str = Field(min_length=1)
    charge: int = 0
    multiplicity: int = 1


@router.post("/orca/optimization")
def create_orca_optimization(request: OrcaOptimizationRequest) -> dict[str, str]:
    return submit_orca_optimization(request)


@router.get("/slurm/{job_id}")
def get_slurm_status(job_id: str) -> dict[str, str]:
    config = load_cluster_config()
    cluster = config["cluster"]
    ssh = ClusterSSHClient(
        host=cluster["host"],
        port=int(cluster.get("port", 22)),
        username=cluster["username"],
        private_key_path=cluster["private_key"],
    )
    status = SlurmClient(ssh).status(job_id)
    return {"job_id": job_id, "status": status}
