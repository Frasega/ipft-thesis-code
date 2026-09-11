#!/bin/bash
# Read the event files and write the surface CSVs. No simulation: this only re-reads
# runs that already exist, so it is cheap to repeat and safe to interrupt.
#
#   sbatch --account=$IPFT_ACCOUNT cluster/job_postprocess.sbatch \
#          ipft_rotterdam_dwell_blocking_pce028_runs sensitivity_rotterdam_dwell_blocking_pce028
#
# Each cell runs in its own subprocess (memory is released when it exits, and one
# cell's failure does not take the batch down), so --jobs is real parallelism and the
# 8 CPUs asked for here are used.
#
# MEMORIA. Su compute-p1 il tetto e' 3.996 MB per CPU (48 CPU, 185 GB per nodo), e una
# richiesta sopra il tetto viene respinta da sbatch, non ridotta. La memoria si ottiene
# chiedendo piu' CPU. Scoperto l'11/9/2026 al primo lancio vero.
#SBATCH --job-name=ipft-post
#SBATCH --partition=compute-p1   # 'compute' e' in drain: zero nodi
#SBATCH --time=03:00:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=12
#SBATCH --mem-per-cpu=3900M   # 12 x 3,9 GB = 47 GB: 8 celle analizzate insieme
#SBATCH --output=logs/post-%j.out

set -u
. "$SLURM_SUBMIT_DIR/cluster/env.sh"
ipft_check || exit 1

RUNS="${1:?usage: job_postprocess.sbatch <runs dir name> <output dir name>}"
OUT="${2:?usage: job_postprocess.sbatch <runs dir name> <output dir name>}"

# --dwell-in-matsim tells the bus term to measure the standing time from the stop
# events instead of charging it a priori. Pointing it at pre-dwell runs would collapse
# the idle component to ~0 without saying so, which is why the flag is explicit and why
# test_dwell_idle_guard.py exercises that branch.
srun python python_pipeline/rotterdam_surface_robust.py \
    --runs-dir "$IPFT_OUTPUT_ROOT/$RUNS" \
    --out "output/$OUT" \
    --dwell-in-matsim \
    --jobs 8

echo
echo "results in $IPFT_PROJECT/output/$OUT - copy them off scratch, it is not backed up:"
echo "  scp -r <cluster>:$IPFT_PROJECT/output/$OUT ."
