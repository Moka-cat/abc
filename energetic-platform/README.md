# Energetic Platform

Automation platform for ORCA and VASP workflows on a Slurm cluster.

The platform does not package ORCA, VASP, or POTCAR files. It runs as Docker
services and talks to the cluster over SSH, where the licensed software is
already deployed.

## First Milestone

- Submit one ORCA geometry optimization from a web page.
- Generate ORCA input and Slurm script automatically.
- Submit the job through SSH with `sbatch --parsable`.
- Store the Slurm job id and show status in the API/frontend.
- Keep VASP configuration and templates ready for the next milestone.

## Quick Start

```bash
cp .env.example .env
cp config/cluster.example.yaml config/cluster.yaml
docker compose up -d --build
```

Open:

```text
http://localhost:8501
```

Before real cluster submission, edit:

- `.env`
- `config/cluster.yaml`

You must confirm the cluster login host, SSH key, Slurm partition, ORCA path,
ORCA environment script, VASP POTCAR root, and scratch/work directories.

## Architecture

```text
Browser
  -> Streamlit frontend
  -> FastAPI backend
  -> SSH
  -> Slurm login node
  -> ORCA / VASP on compute nodes
```

## Important License Boundary

Do not put these into the Docker image:

- `vasp_std`, `vasp_gam`, `vasp_ncl`
- VASP source code
- POTCAR files
- ORCA binaries

The platform only generates input files, submits jobs, monitors status, parses
outputs, and records results.
