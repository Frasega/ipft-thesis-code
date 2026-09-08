#!/bin/bash
# The sensitivity surface: 60 one-iteration runs (5 load rates x 2 demand levels x
# 3 parcel weights x 2 seeds), each branching from the frozen equilibrium.
#
#   bash cluster/list_configs.sh RDWELLBLOCKING > configs.txt
#   sbatch --account=$IPFT_ACCOUNT cluster/job_surface.sbatch configs.txt
#
# SHAPE, AND WHY IT IS NOT --array=0-59. A student (education) account on DelftBlue may
# have 8 jobs RUNNING at a time. One array task per run is 60 jobs, so they would come
# through 8 at a time: eight waves, three and a half hours. Eight fat tasks that each
# run 8 configs concurrently give 64 slots for 60 runs - one wave, about 40 minutes -
# and stay inside the 64-CPU-per-job cap at the same time.
#
# THE ARITHMETIC PER TASK. 24 CPUs and 96 GB. Each run gets 3 CPUs (a simulation thread
# plus an event-processing thread that measures at ~90% of a core, plus margin) and a
# 9 GB heap; 8 x 9 GB = 72 GB of heap inside 96 GB, leaving room for the JVMs
# themselves. Raising the concurrency without raising the memory is how the collector
# starts thrashing, and a thrashing run does not fail - it just takes three times longer.
#
# RESUMABLE. --skip-existing makes a task step over any run that already wrote its
# events file, so the array can be resubmitted after a partial failure and only the
# missing cells are redone.
#
#SBATCH --job-name=ipft-surface
#SBATCH --partition=compute
#SBATCH --time=04:00:00
#SBATCH --array=0-7
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=24
#SBATCH --mem-per-cpu=4G
#SBATCH --output=logs/surface-%A_%a.out

set -u
. "$SLURM_SUBMIT_DIR/cluster/env.sh"
ipft_check || exit 1

LIST="${1:-configs.txt}"
CONCURRENCY="${IPFT_CONCURRENCY:-8}"
HEAP="${IPFT_HEAP:-9g}"
NTASKS="${SLURM_ARRAY_TASK_COUNT:-8}"
ME="${SLURM_ARRAY_TASK_ID:-0}"

[ -f "$LIST" ] || { echo "no config list: $LIST"; exit 1; }
mapfile -t CONFIGS < "$LIST"
TOTAL=${#CONFIGS[@]}
echo "[surface] task $ME of $NTASKS, $TOTAL configs, $CONCURRENCY at a time, heap $HEAP"

# Round-robin rather than contiguous blocks: the cells differ in cost (peak runs take
# longer than off-peak ones, and they are adjacent in the list), so contiguous slices
# would leave some tasks finishing an hour before others.
for ((i = ME; i < TOTAL; i += NTASKS)); do
    echo "${CONFIGS[$i]}"
done | xargs -P "$CONCURRENCY" -I{} \
    python python_pipeline/scenario_runner.py --config {} --heap "$HEAP" --skip-existing

echo "[surface] task $ME finished with status $?"
