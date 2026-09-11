#!/bin/bash
# Warm plans and configurations for the surface, from the frozen plans of an
# equilibration that has ALREADY been stripped - locally or by job_prep.sbatch.
#
#   sbatch --account=$IPFT_ACCOUNT cluster/job_warmgen.sbatch [ptVehiclePCE028.xml]
#
# WHY NOT job_prep.sbatch. That one starts by stripping the equilibrated plans, so it
# looks for the unstripped output_plans.xml.zst. When the equilibration ran elsewhere and
# only its stripped plans were copied over, that file is not here and job_prep stops at
# its first step. This job starts from the stripped plans.
#
# WHY A JOB. create_plans_file parses the whole population into memory (256,447 agents
# at peak): several GB and minutes of CPU, which is what a login node is not for.
#
# MEMORIA. Su compute-p1 il tetto e' 3.996 MB per CPU (48 CPU, 185 GB per nodo), e una
# richiesta sopra il tetto viene respinta da sbatch, non ridotta. La memoria si ottiene
# chiedendo piu' CPU. Scoperto l'11/9/2026 al primo lancio vero.
#SBATCH --job-name=ipft-warmgen
#SBATCH --partition=compute-p1   # 'compute' e' in drain: zero nodi
#SBATCH --time=02:00:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=12
#SBATCH --mem-per-cpu=3900M   # 12 x 3,9 GB = 47 GB: la popolazione intera in memoria
#SBATCH --output=logs/warmgen-%j.out

set -u
. "$SLURM_SUBMIT_DIR/cluster/env.sh"
ipft_check || exit 1

PTVEH="${1:-ptVehiclePCE028.xml}"

echo "=== in-lane stop (blocking) ==="
python python_pipeline/make_rotterdam_warm_scenarios.py --dwell-tag blocking --pt-vehicles "$PTVEH" || exit 1

# The warm plans depend on alpha, demand level and parcel weight - not on the stop
# variant - so the bay run reuses the files the blocking run just wrote.
echo "=== bus bay (reuses the same warm plans) ==="
python python_pipeline/make_rotterdam_warm_scenarios.py --dwell-tag bay --pt-vehicles "$PTVEH" || exit 1

echo "=== counts ==="
echo "in-lane configs: $(ls scenarios/ipft_rotterdam/generated/config_RDWELLBLOCKINGPCE028_*.xml 2>/dev/null | wc -l)  (expected 60)"
echo "bay configs:     $(ls scenarios/ipft_rotterdam/generated/config_RDWELLBAYPCE028_*.xml 2>/dev/null | wc -l)  (expected 60)"
echo "warm plans:      $(ls "$IPFT_OUTPUT_ROOT"/ipft_rotterdam_warm_plans/*.xml.gz 2>/dev/null | wc -l)  (expected 30)"
