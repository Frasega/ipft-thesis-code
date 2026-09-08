#!/bin/bash
# The equilibration: 80 iterations of the background population with zero vans, the run
# every scenario branches from. Submit it TWICE, once per demand level - they are
# independent, so they run side by side and the pair costs one wall time, not two.
#
#   sbatch --account=$IPFT_ACCOUNT cluster/job_longbase.sbatch peak    [ptVehiclePCE028.xml]
#   sbatch --account=$IPFT_ACCOUNT cluster/job_longbase.sbatch offpeak [ptVehiclePCE028.xml]
#
# WALL TIME. Measured on the author's workstation (i5-12450H): peak 11 h 10 for 81
# iterations, off-peak 5 h 37. A Xeon 6248R core is roughly 1.3-1.6x slower, which puts
# the peak at 15-18 h against the 24 h cap of a DelftBlue education account. There is no
# checkpoint: a job killed at the cap loses everything. So, one hour in, check the pace:
#
#   grep "ITERATION .* ENDS" $IPFT_OUTPUT_ROOT/ipft_rotterdam_longbase*/*.logfile.log | tail -3
#
# Above ~13 min/iteration the 80 will not fit. Cancel, and run it in two halves instead,
# the second starting from the first's output_plans.
#
# CPUS. Three, not one. MATSim is not single-threaded even with no numberOfThreads in
# the config: it runs a separate event-processing thread, which in a measured run burned
# 594 s of CPU inside an 11-minute iteration - about 90% of a core, alongside the
# simulation thread.
#
#SBATCH --job-name=ipft-longbase
#SBATCH --partition=compute
#SBATCH --time=24:00:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=3
#SBATCH --mem-per-cpu=5G
#SBATCH --output=logs/longbase-%j.out

set -u
. "$SLURM_SUBMIT_DIR/cluster/env.sh"
ipft_check || exit 1

CONGESTION="${1:-peak}"
PTVEH="${2:-}"
PTFLAG=""
[ -n "$PTVEH" ] && PTFLAG="--pt-vehicles $PTVEH"

# Build the config here rather than assuming one is lying around: the two that used to
# be committed carried absolute Windows paths and whichever bus PCE was current when
# they were written.
# shellcheck disable=SC2086
python scenarios/ipft_rotterdam/make_longbase_config.py \
    --congestion "$CONGESTION" $PTFLAG | tee "$TMPDIR/longbase.out" || exit 1

# The generator names the file it wrote; a glob would not do, because once a second PCE
# campaign exists two names match and which one comes last is an accident of ordering.
CFG=$(awk -v c="$CONGESTION" '$1=="LONGBASE_CONFIG" && $2==c {print $3}' "$TMPDIR/longbase.out")
[ -f "$CFG" ] || { echo "[longbase] no config produced for $CONGESTION"; exit 1; }
echo "[longbase] config: $CFG"

srun python python_pipeline/scenario_runner.py --config "$CFG" --heap 12g --skip-existing
