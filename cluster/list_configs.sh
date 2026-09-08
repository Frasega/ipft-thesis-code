#!/bin/bash
# Print the configs a campaign consists of, one per line, for cluster/job_surface.sbatch.
#
#   bash cluster/list_configs.sh RDWELLBLOCKING           > configs.txt
#   bash cluster/list_configs.sh RDWELLBLOCKINGPCE028     > configs_pce028.txt
#
# The prefix is matched as a whole word between "config_" and the run name, NOT as a
# substring: "RDWELLBLOCKING" must not pull in "RDWELLBLOCKINGPCE028", or a campaign at
# one bus PCE would quietly run the other one's configs too.
set -u

. "$(dirname "$0")/env.sh" >/dev/null 2>&1

PREFIX="${1:?usage: list_configs.sh <CONFIG PREFIX> [generated dir]}"
GEN="${2:-$IPFT_PROJECT/scenarios/ipft_rotterdam/generated}"

found=0
for f in "$GEN"/config_"$PREFIX"_*.xml; do
    [ -e "$f" ] || continue
    echo "$f"
    found=$((found + 1))
done

if [ "$found" -eq 0 ]; then
    echo "no configs named config_${PREFIX}_*.xml in $GEN" >&2
    echo "generate them first with make_rotterdam_warm_scenarios.py" >&2
    exit 1
fi
echo "$found configs" >&2
