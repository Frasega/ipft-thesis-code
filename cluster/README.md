# Running the campaign on an HPC cluster

Written for DelftBlue (TU Delft), but nothing here is DelftBlue-specific except the
default module names and the node arithmetic: every path comes from an environment
variable, so the same scripts run anywhere SLURM does.

The campaign is ~60 MATSim runs plus two equilibrations. On one workstation that is
two days with the machine unusable; on a cluster the 60 runs are one wave of about
forty minutes and the two equilibrations run side by side overnight.

---

## What has to be transferred, and how

Two things, and only one of them is in git.

**The code** — this repository. Clone it, or `rsync` your working tree:

```bash
rsync -avz --exclude 'target/' --exclude 'output/' --exclude '__pycache__/' \
      --exclude '*.jar' --exclude 'scenarios/*/generated/' \
      /path/to/matsim-example-project-master/ <user>@<cluster>:~/ipft/
```

`scenarios/*/generated/` is excluded deliberately: those configs contain absolute paths
from the machine that wrote them, and they are rebuilt on the cluster in seconds.

**The scenario data** — ~580 MB, not in git and not ours to publish. The Rotterdam
network, population and timetable come from the XCARCITY project; the HBEFA factor
tables are a licensed dataset. All of it goes in `scenarios/ipft_rotterdam/`:

| file | size |
|---|---|
| `networkWithRideAndBike.xml.gz` | 30 MB |
| `planExternalProcessed_lowerCase.xml.gz` | 29 MB |
| `plans_offpeak_base.xml.gz` | 14 MB |
| `ptSchedule36Hour.xml.gz` | 3.3 MB |
| `ptVehicleExtended.xml`, `ptVehiclePCE028.xml` | 1.6 MB each |
| `config.xml` | 1.4 MB |
| `emission_vehicles_rotterdam.xml`, `sample_41_EFA_*.csv`, link sets | small |

`cluster/bootstrap.sh` checks for every one of them and refuses to finish if any is
missing, because a missing input is the kind of thing that surfaces four hours into a
job rather than at the start.

The jar is **not** transferred: 219 MB, and it is rebuilt on the cluster.

---

## Setup, once

```bash
ssh <user>@<cluster>            # DelftBlue: login.delftblue.tudelft.nl, eduVPN if off campus
cd ~/ipft
bash cluster/bootstrap.sh       # ON THE LOGIN NODE — this is the only step needing internet
```

It installs a JDK 25 in `$HOME`, creates a virtual environment, writes
`config/machine.yaml`, and builds the jar. Fifteen minutes, mostly Maven.

Two things it must be a login node for: compute nodes are firewalled on most clusters,
so neither Maven nor `pip` can reach anything from inside a job.

To point it somewhere else, set any of these before running it (or put them in
`~/.ipft_env`, which `cluster/env.sh` sources):

```bash
export IPFT_SCRATCH=/scratch/$USER      # big, fast, not backed up
export IPFT_OUTPUT_ROOT=$IPFT_SCRATCH/TesiOutputs
export IPFT_JAVA_HOME=$HOME/jdk-25
export IPFT_MODULES="2026 cpu python"   # empty string to load none
export IPFT_ACCOUNT=education-<faculty>-<programme>
export IPFT_PARTITION=compute
```

Find your account name with:

```bash
sacctmgr list user $USER withassoc format='user%-20,account%-45,maxjobs,maxwall'
```

That command also tells you the two limits that decide the shape of the job array:
how many jobs may run at once, and the maximum wall time.

---

## The campaign, in order

Everything below assumes `IPFT_ACCOUNT` is set. Drop the `--account` flag if your
cluster does not use one.

```bash
cd ~/ipft
export IPFT_ACCOUNT=<yours>
```

### 1. Equilibration — 80 iterations, no vans, both demand levels at once

```bash
sbatch --account=$IPFT_ACCOUNT cluster/job_longbase.sbatch peak    ptVehiclePCE028.xml
sbatch --account=$IPFT_ACCOUNT cluster/job_longbase.sbatch offpeak ptVehiclePCE028.xml
```

Drop the last argument to equilibrate at the timetable's own bus PCE (2.8) instead of
the sampled-capacity one (0.28). The two produce separate configs, separate output
directories and separate campaigns; they never overwrite each other.

Expect 15-18 h for the peak and 7-9 h for the off-peak. **One hour in, check the pace:**

```bash
grep "ITERATION .* ENDS" $IPFT_OUTPUT_ROOT/ipft_rotterdam_longbase*/*.logfile.log | tail -3
```

Above ~13 minutes per iteration, 80 iterations will not fit inside a 24 h limit. Cancel
and run it in two halves, the second starting from the first's `output_plans`.

### 2. Preparation — freeze the day, build the timetables and the configs

```bash
sbatch --account=$IPFT_ACCOUNT cluster/job_prep.sbatch ptVehiclePCE028.xml
```

Forty minutes. It strips the equilibrated plans to one per agent (otherwise MATSim's
plan selector re-picks between baseline and scenario, and on the bus-stop links that
noise is the size of the signal), writes the ten dwell timetables, and generates the
configs for both stop variants.

### 3. The surface — 60 runs in one wave

```bash
bash cluster/list_configs.sh RDWELLBLOCKINGPCE028 > configs.txt
sbatch --account=$IPFT_ACCOUNT cluster/job_surface.sbatch configs.txt
```

Eight tasks, 24 CPUs each, eight runs at a time inside each. About forty minutes.
Resubmit the same command after a partial failure: `--skip-existing` steps over every
run that already wrote its events file.

For the bus-bay variant, list `RDWELLBAYPCE028` instead and submit again.

### 4. Post-processing

```bash
sbatch --account=$IPFT_ACCOUNT cluster/job_postprocess.sbatch \
       ipft_rotterdam_dwell_blocking_pce028_runs \
       sensitivity_rotterdam_dwell_blocking_pce028
```

Then copy `output/` off the cluster. Scratch is not backed up and is purged.

---

## The numbers behind the job sizes

Measured, not guessed — all of it from the run logs of the campaign as it exists:

| | |
|---|---|
| one warm run | 23.2 min wall, 2 iterations of ~11 min |
| its heap | peaks at 6798 MB inside a 7168 MB ceiling — 370 MB of headroom, hence 9 GB here |
| equilibration, peak | 11 h 10 for 81 iterations (8.3 min each), 8 GB heap peaking at 7.3 |
| equilibration, off-peak | 5 h 37, 6 GB heap peaking at 5.3 |
| events file per run | 590 MB compressed → ~35 GB for 60 runs |
| threads per run | 2 in practice: the event-processing thread alone burned 594 s of CPU inside an 11-minute iteration |

Those wall times are from an i5-12450H. A Xeon 6248R core is roughly 1.3-1.6x slower,
which is where the 15-18 h estimate for the peak equilibration comes from. Measure it
yourself on the first run rather than trusting the factor.

A DelftBlue compute node is 48 cores and 185 GB of usable RAM, so the binding constraint
on how many runs share a node is memory, not cores: 8 runs x 9 GB of heap per 24-CPU
task, three tasks to a node.

---

## When something goes wrong

| symptom | cause |
|---|---|
| `UnsupportedClassVersionError` | the JDK on PATH is older than 25. `echo $JAVA_HOME`; re-run `bootstrap.sh` |
| job dies instantly, no output | `--mem-per-cpu` unset; on DelftBlue it defaults to 1 MB |
| runs get slower and slower | undersized heap. It never fails, it thrashes the collector. Check `max: NNNN MB` in the run's own logfile |
| `no such file: .../output_plans...` from the warm generator | the equilibration it wants has not run, or ran under a different PCE tag. There is deliberately no fallback: branching off the wrong equilibrium produces numbers that look right |
| the whole array runs 8 tasks at a time | that is the account's job limit, not a bug — see `sacctmgr` above |
| `./mvnw: Permission denied` | `chmod +x mvnw`, or `sh ./mvnw` |
| disk fills up | `ITERS/` is not being pruned. Every run should leave ~600 MB, not 3 GB |
