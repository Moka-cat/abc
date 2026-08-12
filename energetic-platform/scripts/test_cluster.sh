#!/usr/bin/env bash
set -euo pipefail

echo "===== BASIC ====="
hostname
whoami
pwd
uname -a

echo "===== SLURM ====="
which sbatch || true
which squeue || true
which sacct || true
sinfo || true

echo "===== VASP ENV ====="
source /data/zhaoyingying_migration/software_private/vasp-6.3.0-bin/vasp_env_pt.sh
echo "VASP_HOME=${VASP_HOME:-}"
which vasp_std || true
which vasp_gam || true
which vasp_ncl || true

VASP_EXE=$(which vasp_std 2>/dev/null || true)
if [ -n "$VASP_EXE" ]; then
    echo "VASP executable: $VASP_EXE"
    ls -lh "$VASP_EXE"
    ldd "$VASP_EXE" | grep "not found" || true
else
    echo "vasp_std not found"
fi

echo "===== ORCA ====="
which orca || true
which mpirun || true
mpirun --version 2>/dev/null | head || true

echo "===== STORAGE ====="
df -h /data || true
echo "TMPDIR=${TMPDIR:-}"
