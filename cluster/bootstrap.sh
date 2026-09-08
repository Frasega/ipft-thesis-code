#!/bin/bash
# One-shot setup, to be run ONCE on a LOGIN node.
#
#   bash cluster/bootstrap.sh
#
# It must be the login node and not a compute node: this is the only step that needs
# the internet. Compute nodes on DelftBlue (and on most clusters) are firewalled, so
# neither Maven nor pip can reach anything from inside a job. Everything downloaded
# here is cached on disk and used offline afterwards.
#
# It is idempotent: each step checks whether it is already done. Re-run it after a
# change to the Java sources to rebuild the jar.
set -u

. "$(dirname "$0")/env.sh"

JDK_URL="${IPFT_JDK_URL:-https://aka.ms/download-jdk/microsoft-jdk-25.0.2-linux-x64.tar.gz}"
MAVEN_REPO="${IPFT_MAVEN_REPO:-$IPFT_SCRATCH/.m2}"

echo "=== 1/5  JDK 25 ==============================================="
# The pom asks for release 25 and the jar the thesis results were produced with was
# built with a JDK 25 (Microsoft build). Cluster module systems typically top out at
# 11, 17 or 21, and a jar built for 25 refuses to start on those with
# UnsupportedClassVersionError - so the JDK is installed in $HOME rather than the
# release level being lowered, which would produce a different jar from the one the
# reported numbers came from.
if [ -x "$IPFT_JAVA_HOME/bin/java" ]; then
    echo "already there: $IPFT_JAVA_HOME"
else
    tmp=$(mktemp -d)
    echo "downloading $JDK_URL"
    curl -fsSL "$JDK_URL" -o "$tmp/jdk.tar.gz" || {
        echo "download failed. If this cluster has no direct internet access, fetch the"
        echo "tarball yourself, scp it over, untar it into $IPFT_JAVA_HOME and re-run."
        exit 1; }
    mkdir -p "$IPFT_JAVA_HOME"
    tar -xzf "$tmp/jdk.tar.gz" -C "$IPFT_JAVA_HOME" --strip-components=1
    rm -rf "$tmp"
    export JAVA_HOME="$IPFT_JAVA_HOME"
    export PATH="$JAVA_HOME/bin:$PATH"
fi
java -version 2>&1 | head -1

echo
echo "=== 2/5  Python environment ==================================="
if [ -f "$IPFT_VENV/bin/activate" ]; then
    echo "already there: $IPFT_VENV"
else
    python -m venv "$IPFT_VENV" || exit 1
fi
# shellcheck disable=SC1091
. "$IPFT_VENV/bin/activate"
python -m pip install --upgrade pip >/dev/null
python -m pip install -r "$IPFT_PROJECT/requirements.txt" || exit 1
python -c "import pandas, numpy, zstandard, yaml; print('[python]', 'deps ok')"

echo
echo "=== 3/5  machine.yaml ========================================="
# The output root is also exported as IPFT_OUTPUT_ROOT by env.sh, which wins over the
# file; the file exists so that a script run by hand, outside a job, behaves the same.
MACHINE="$IPFT_PROJECT/config/machine.yaml"
if [ -f "$MACHINE" ]; then
    echo "already there: $MACHINE (leaving it alone)"
else
    cat > "$MACHINE" <<EOF
# Written by cluster/bootstrap.sh. Machine-specific, git-ignored on purpose.
schema: ipft-machine/1

output_root:
  value: $IPFT_OUTPUT_ROOT
  note: >
    Scratch space. One run is ~600 MB and a campaign is tens of GB, so this must not be
    the home directory: on DelftBlue home is capped at 30 GB. Scratch is not backed up
    and is purged after six months - copy the CSVs out when the campaign finishes.

jar:
  value: matsim-example-project-0.0.1-SNAPSHOT.jar

heap:
  value: 9g
  note: >
    Measured: a Rotterdam warm run peaks at 6.8 GB inside a 7 GB heap, i.e. 370 MB from
    the ceiling, and an undersized heap does not fail - it thrashes the collector and
    the run silently gets slower. 9g leaves room; the equilibration is given 12g on the
    command line.

java_home:
  value: $IPFT_JAVA_HOME
  note: The JDK 25 installed by cluster/bootstrap.sh.
EOF
    echo "written: $MACHINE"
fi

echo
echo "=== 4/5  Maven build =========================================="
# --- the local repository goes on scratch: Maven unpacks thousands of small files and
# --- a home quota is counted in files as well as bytes.
if [ -f "$IPFT_JAR" ]; then
    echo "jar already built: $IPFT_JAR"
    echo "delete it and re-run to rebuild after a Java change"
else
    chmod +x "$IPFT_PROJECT/mvnw" 2>/dev/null
    ( cd "$IPFT_PROJECT" && sh ./mvnw package -DskipTests -Dmaven.repo.local="$MAVEN_REPO" ) || exit 1
fi
ls -la "$IPFT_JAR"

echo
echo "=== 5/5  Scenario data ========================================"
# The heavy inputs are not in the repository: the Rotterdam network, population and
# timetable belong to the XCARCITY project and the HBEFA factor tables are a licensed
# dataset. They are transferred separately - see cluster/README.md.
missing=0
for f in networkWithRideAndBike.xml.gz planExternalProcessed_lowerCase.xml.gz \
         plans_offpeak_base.xml.gz ptSchedule36Hour.xml.gz ptVehicleExtended.xml \
         emission_vehicles_rotterdam.xml config.xml \
         sample_41_EFA_HOT_vehcat_2020average.csv \
         sample_41_EFA_ColdStart_vehcat_2020average.csv; do
    if [ ! -f "$IPFT_PROJECT/scenarios/ipft_rotterdam/$f" ]; then
        echo "  MISSING  scenarios/ipft_rotterdam/$f"
        missing=$((missing + 1))
    fi
done
if [ "$missing" -gt 0 ]; then
    echo "$missing scenario input(s) missing - transfer them before submitting anything."
    exit 1
fi
echo "all scenario inputs present"

echo
ipft_check && echo "bootstrap complete."
