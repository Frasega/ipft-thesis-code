#!/bin/bash
# Everything that has to happen before a simulation can start: the frozen population,
# the dwell timetables and the generated configs. Roughly 40 minutes.
#
#   sbatch --account=$IPFT_ACCOUNT cluster/job_prep.sbatch [ptVehiclePCE028.xml]
#
# The optional argument is the transit vehicles file, i.e. the bus PCE. Passing it
# gives every config and every run directory a tag of its own, so a campaign at a
# corrected PCE sits beside the original instead of overwriting it.
#
# WHY THIS IS A JOB AND NOT A LOGIN-NODE COMMAND. strip_selected_plans and the warm
# plans generator parse the whole population into memory: 250k agents, and the peak
# plans file is 140 MB compressed. That is minutes of CPU and over 10 GB of RAM, which
# is exactly what a login node is not for.
#
# MEMORIA. Su compute-p1 il tetto e' 3.996 MB per CPU (48 CPU, 185 GB per nodo), e una
# richiesta sopra il tetto viene respinta da sbatch, non ridotta. La memoria si ottiene
# chiedendo piu' CPU. Scoperto l'11/9/2026 al primo lancio vero.
#SBATCH --job-name=ipft-prep
#SBATCH --partition=compute-p1   # 'compute' e' in drain: zero nodi
#SBATCH --time=02:00:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem-per-cpu=3900M   # 8 x 3,9 GB = 31 GB: la popolazione intera in memoria
#SBATCH --output=logs/prep-%j.out

set -u
. "$SLURM_SUBMIT_DIR/cluster/env.sh"
ipft_check || exit 1

PTVEH="${1:-}"
PTFLAG=""
[ -n "$PTVEH" ] && PTFLAG="--pt-vehicles $PTVEH"

echo "=== 1/3  freeze the equilibrated day =========================="
# The equilibration ends with five plans per agent, and one warm iteration does not
# stop ChangeExpBeta from re-picking among them: inserting a different number of vans
# shifts the random stream and thousands of background agents choose differently, which
# on the bus-stop links is noise the size of the signal. Keeping only the plan each
# agent was already running removes the re-pick without changing anyone's behaviour.
for tag in "" "_offpeak"; do
    dir="$IPFT_OUTPUT_ROOT/ipft_rotterdam_longbase${tag}${IPFT_LONGBASE_SUFFIX:-}"
    src=$(ls "$dir"/*output_plans.xml.zst 2>/dev/null | head -1)
    dst="${src%.xml.zst}_stripped.xml.gz"
    if [ -z "$src" ]; then
        echo "  no equilibration output in $dir - run cluster/job_longbase.sbatch first"
        exit 1
    fi
    if [ -f "$dst" ]; then
        echo "  already stripped: $(basename "$dst")"
    else
        python python_pipeline/strip_selected_plans.py "$src" "$dst" || exit 1
    fi
done

echo
echo "=== 2/3  dwell timetables ====================================="
# One transit schedule per load rate, with the freight handover written in as
# minimumStopDuration. They depend on alpha only - not on weight, not on demand level -
# so ten files cover the whole surface. The generator refuses to fall back to the plain
# schedule if one is missing, loudly, because a run that silently lost its dwell looks
# valid and reports the wrong bus cost.
python python_pipeline/make_dwell_schedules.py --blocking true  || exit 1
python python_pipeline/make_dwell_schedules.py --blocking false || exit 1

echo
echo "=== 3/3  configs =============================================="
# shellcheck disable=SC2086
python python_pipeline/make_rotterdam_warm_scenarios.py --dwell-tag blocking $PTFLAG || exit 1
# shellcheck disable=SC2086
python python_pipeline/make_rotterdam_warm_scenarios.py --dwell-tag bay      $PTFLAG || exit 1

echo
echo "prep done. Build the config list for the array with:"
echo "  bash cluster/list_configs.sh RDWELLBLOCKING${PTVEH:+<TAG>} > configs.txt"
