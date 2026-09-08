# Sourced by every job script and by bootstrap.sh. Nothing here is specific to one
# person's account: every path comes from an environment variable with a default that
# works on DelftBlue, so the same scripts run on any SLURM cluster by overriding the
# handful of names below.
#
#   source cluster/env.sh
#
# Override in your shell (or in ~/.ipft_env, which is sourced first if it exists):
#
#   IPFT_PROJECT     where this repository is checked out   default: the repo itself
#   IPFT_SCRATCH     big, fast, NOT backed up               default: /scratch/$USER
#   IPFT_OUTPUT_ROOT where MATSim writes its runs           default: $IPFT_SCRATCH/TesiOutputs
#   IPFT_VENV        the Python virtual environment         default: $IPFT_SCRATCH/ipft-venv
#   IPFT_JAVA_HOME   a JDK 25                               default: $HOME/jdk-25
#   IPFT_MODULES     module names to load                   default: "2026 cpu python"
#   IPFT_ACCOUNT     SLURM account for --account            default: unset (sbatch decides)
#   IPFT_PARTITION   SLURM partition                        default: compute
#
# The one that must be right is IPFT_OUTPUT_ROOT. A MATSim run writes ~600 MB and a
# campaign is tens of GB: on a home directory with a 30 GB quota it fills the quota
# part-way through a job array, and every subsequent run fails with a disk error that
# looks like a MATSim bug.

[ -f "$HOME/.ipft_env" ] && . "$HOME/.ipft_env"

# The repository root, resolved from this file rather than from the caller's cwd, so
# that sourcing it from a job script that SLURM started in an arbitrary directory
# still finds the project.
_ipft_env_dir=$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)
export IPFT_PROJECT="${IPFT_PROJECT:-$(dirname "$_ipft_env_dir")}"

# $USER is not guaranteed: it is unset in some batch environments and in Git Bash on
# Windows, and under `set -u` - which every job script here uses - an unset variable
# aborts the script at the point of use, i.e. before anything has been printed.
IPFT_USER="${USER:-${LOGNAME:-$(id -un 2>/dev/null || echo user)}}"
export IPFT_USER

export IPFT_SCRATCH="${IPFT_SCRATCH:-/scratch/$IPFT_USER}"
export IPFT_OUTPUT_ROOT="${IPFT_OUTPUT_ROOT:-$IPFT_SCRATCH/TesiOutputs}"
export IPFT_VENV="${IPFT_VENV:-$IPFT_SCRATCH/ipft-venv}"
export IPFT_JAVA_HOME="${IPFT_JAVA_HOME:-$HOME/jdk-25}"
export IPFT_MODULES="${IPFT_MODULES-2026 cpu python}"
export IPFT_PARTITION="${IPFT_PARTITION:-compute}"

# Modules: absent on a plain workstation, and a cluster that does not use Lmod is not
# an error either - the JDK and the venv are found by path, not by module.
if [ -n "$IPFT_MODULES" ] && command -v module >/dev/null 2>&1; then
    # shellcheck disable=SC2086
    module load $IPFT_MODULES 2>/dev/null || \
        echo "[env] WARNING: 'module load $IPFT_MODULES' failed - carrying on with what is on PATH"
fi

if [ -d "$IPFT_JAVA_HOME" ]; then
    export JAVA_HOME="$IPFT_JAVA_HOME"
    export PATH="$JAVA_HOME/bin:$PATH"
fi

if [ -f "$IPFT_VENV/bin/activate" ]; then
    # shellcheck disable=SC1091
    . "$IPFT_VENV/bin/activate"
fi

# Python buffers its output when stdout is a file, which in a batch job means the log
# stays empty for hours and a job that is stuck looks identical to one that is working.
export PYTHONUNBUFFERED=1

export IPFT_JAR="${IPFT_JAR:-$IPFT_PROJECT/matsim-example-project-0.0.1-SNAPSHOT.jar}"

# Node-local scratch. SLURM sets it on most clusters; not all, and a job script that
# assumes it silently writes to an empty path.
export TMPDIR="${TMPDIR:-/tmp}"
mkdir -p "$IPFT_PROJECT/logs" 2>/dev/null

cd "$IPFT_PROJECT" || exit 1

ipft_check() {
    # Fail before a 16-hour job discovers the problem, not during it.
    local bad=0
    command -v java >/dev/null 2>&1 || { echo "[env] no java on PATH"; bad=1; }
    if command -v java >/dev/null 2>&1; then
        java -version 2>&1 | head -1 | sed 's/^/[env] java: /'
    fi
    command -v python >/dev/null 2>&1 || { echo "[env] no python on PATH"; bad=1; }
    [ -f "$IPFT_JAR" ] || { echo "[env] jar not found: $IPFT_JAR (run cluster/bootstrap.sh)"; bad=1; }
    mkdir -p "$IPFT_OUTPUT_ROOT" 2>/dev/null || { echo "[env] cannot create $IPFT_OUTPUT_ROOT"; bad=1; }
    echo "[env] project=$IPFT_PROJECT"
    echo "[env] output =$IPFT_OUTPUT_ROOT"
    return $bad
}
