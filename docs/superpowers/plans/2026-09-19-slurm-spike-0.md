# Slurm Spike 0: find out how Slurm really behaves, before we build the agent

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Answer, with evidence from a real Slurm, the nine questions in the spec that the agent design depends on, and keep the real command output as fixtures for the agent's future tests.

**Architecture:** Run the open-source `giovtorres/slurm-docker-cluster` (MIT) in Docker on this machine. Fake GPUs are configured as generic resources. Each experiment is a short script of Slurm commands; every output is saved to `agent/tests/fixtures/slurm/`. The result is a findings document plus edits to the spec wherever reality contradicts an assumption. **No product code is written and no existing app code is touched.**

**Tech Stack:** Docker Desktop (installed: 29.4.0), Docker Compose, Slurm (the version the image ships), PowerShell 5.1 on the host, bash in the containers.

**Spec:** `docs/superpowers/specs/2026-09-19-slurm-agent-automation-design.md` (sections 7, 8, 10 and 16 are what this plan tests).

## Global Constraints

- **Ask before every download.** State the file, source and size, and wait for a clear yes (image pull in Task 1; nothing else should need one).
- **Do not commit** unless the user asks. No commits exist in this repo yet.
- **Nothing installed on the host beyond Docker containers.** The clone of the cluster repo lives in the scratch folder, outside the OneDrive-synced project.
- **No secrets** are involved. The cluster's throwaway passwords stay inside the containers.
- **Scripts reach the containers as files** (`put`), never as `docker exec` arguments, because Windows PowerShell 5.1 mangles embedded quotes.
- **Times are UTC** in every recorded output (the containers run in UTC; Task 5 confirms it).
- **Existing app code is untouched** (`backend/`, `frontend/`). New files go only in `docs/superpowers/spikes/` and `agent/tests/fixtures/slurm/`.
- **Every finding cites the recorded output file that proves it.** No finding without evidence.
- **The spec's safety rule stays fixed:** we never *hold* a job; we only set a start time. Task 3 tests that this works; if it does not, the fallback is a design change to raise with the user, not a quiet substitution.

## The nine questions this spike answers

| # | Question (from spec section 16) | Decision it drives |
|---|---|---|
| Q1 | Does setting `StartTime` on a pending job work, and does Slurm then start it on time (even while resources are free)? | The whole control mechanism. If not: fall back to hold/release and revisit safety section 8. |
| Q2 | Is `squeue --start` (predicted start) available and usable as the "baseline start"? | Baseline for savings (section 10). If unreliable: baseline = submit time. |
| Q3 | Which `sacct` fields give real start, end, elapsed, state, GPU count? | What the finished-job reporter sends. |
| Q4 | How are GPUs counted (TRES `gres/gpu`) for pending and finished jobs? | The `gpus` field of the sync request. |
| Q5 | Is per-job energy available? | Measured power (deferred) or modeled power only. |
| Q6 | Can times be read unambiguously (epoch/UTC), and is JSON output available? | Parsing approach in the agent. |
| Q7 | Does a decision survive a controller restart? | The "cloud/agent down, job still starts" guarantee. |
| Q8 | How does a user's own change (StartTime, hold, cancel) show up, and can we mark our own jobs? | "Never fight the user" rule. |
| Q9 | What privilege does the agent need, and how do arrays, dependencies and requeues look? | Least-privilege setup; which jobs the agent must skip in v1. |

## File Structure

- Create: `docs/superpowers/spikes/2026-09-19-slurm-spike-findings.md`: the answers, with evidence links.
- Create: `agent/tests/fixtures/slurm/*.txt`: real recorded command output (later used by the agent's unit tests).
- Modify: `docs/superpowers/specs/2026-09-19-slurm-agent-automation-design.md`: section 16 gets the verified results; any wrong assumption is corrected in the section that made it.
- Scratch (not in the project): `<scratchpad>\slurm-lab\` holds the cluster clone and a helper script `lib.sh`.

---

### Task 1: Bring up a real Slurm cluster

**Files:**
- Create (scratch): `<scratchpad>\slurm-lab\` (a clone), `<scratchpad>\slurm-lab\lib.sh`
- Create: `agent/tests/fixtures/slurm/00-cluster-info.txt`

**Interfaces:**
- Produces: a running cluster; PowerShell variable `$C` (the controller container name) and function `sx` (run a bash command in the controller); `lib.sh` with `wait_state` and `stamp`, used by every later task.

- [ ] **Step 1: Check what is already running and how much the image weighs**

```powershell
docker version --format '{{.Server.Version}}' 2>&1
docker ps -a --format '{{.Names}}' 2>&1
```
Expected: either a server version, or an error saying the daemon is not running (then do Step 2). Nothing about Slurm should be listed.

- [ ] **Step 2: Start Docker Desktop if the daemon is down**

```powershell
Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"
$ok = $false
foreach ($i in 1..60) { docker info *> $null; if ($LASTEXITCODE -eq 0) { $ok = $true; break }; Start-Sleep -Seconds 5 }
"docker daemon up: $ok"
```
Expected: `docker daemon up: True` within about five minutes (first start also boots WSL).

- [ ] **Step 3: Find the image size and ask the user for permission to pull it**

```powershell
docker manifest inspect giovtorres/slurm-docker-cluster:latest 2>&1 | Select-String -Pattern '"size"' | Select-Object -First 5
```
Then ask the user (AskUserQuestion): "May I pull the Docker image `giovtorres/slurm-docker-cluster:latest` (MIT-licensed, from Docker Hub, about N MB compressed per the manifest, plus its MySQL image) and clone its small GitHub repository into the scratch folder?" **Stop until the user says yes.**

- [ ] **Step 4: Clone the repo and pull the images (after the yes)**

```powershell
$lab = "<scratchpad>\slurm-lab"            # replace <scratchpad> with this session's scratchpad path
git clone https://github.com/giovtorres/slurm-docker-cluster.git $lab
Set-Location $lab
Copy-Item .env.example .env
docker pull giovtorres/slurm-docker-cluster:latest
```
Expected: a `docker-compose.yml` and `.env` in `$lab`; the pull completes.

- [ ] **Step 5: Start the cluster (plain `docker compose`, because `make` may not exist on Windows)**

```powershell
docker compose up -d
docker compose ps --format "table {{.Name}}\t{{.Status}}"
```
Expected: `mysql`, `slurmdbd`, `slurmctld`, `slurmrestd`, `c1`, `c2` all `running`/`healthy`. If a service name differs, use the names printed here in every later command.

- [ ] **Step 6: Define the helpers and confirm Slurm answers**

Create `$lab\lib.sh`:

```bash
stamp() { date -u +%Y-%m-%dT%H:%M:%SZ; }

# wait_state <jobid> <STATE> <timeout_seconds>: poll until the job reaches STATE, print how long it took.
wait_state() {
  local id=$1 want=$2 t=$3 s=""
  for i in $(seq 1 "$t"); do
    s=$(squeue -j "$id" -h -o %T 2>/dev/null)
    [ -z "$s" ] && s=$(sacct -j "$id" -X -n -P -o State 2>/dev/null | head -1)
    if [ "$s" = "$want" ]; then echo "job $id reached $want after ${i}s at $(stamp)"; return 0; fi
    sleep 1
  done
  echo "TIMEOUT: job $id last state '$s' at $(stamp) (wanted $want)"; return 1
}
```

Then, in PowerShell:

```powershell
$C = "slurmctld"                                   # the controller container name from Step 5
docker cp "$lab\lib.sh" "${C}:/root/lib.sh"

# put NAME TEXT: write TEXT into the container as /root/NAME with Unix line endings.
# Scripts go in as FILES because Windows PowerShell 5.1 mangles quotes passed to `docker exec` directly.
function put($name, $text) { $text.Replace("`r`n", "`n") | docker exec -i $C bash -c "cat > /root/$name" }

# sx TEXT: run a block of bash in the controller (lib.sh already sourced), from a file, so quoting never matters.
function sx($cmd) { put "_sx.sh" ("source /root/lib.sh`n" + $cmd); docker exec $C bash -l /root/_sx.sh }

sx 'sinfo; echo ---; scontrol --version; echo ---; sbatch --wrap="hostname" -t 1'
```
Expected: `sinfo` lists a partition with nodes `c1,c2` in `idle`; a version line such as `slurm 25.xx`; `Submitted batch job 1`.

- [ ] **Step 7: Record the environment as a fixture**

```powershell
New-Item -ItemType Directory -Force "C:\Users\aakur\OneDrive\Desktop\WattShift\agent\tests\fixtures\slurm" | Out-Null
$fx = "C:\Users\aakur\OneDrive\Desktop\WattShift\agent\tests\fixtures\slurm"
sx 'echo "recorded: $(stamp)"; scontrol --version; echo; sinfo; echo; scontrol show config | grep -Ei "^(SchedulerType|SelectType|SelectTypeParameters|PriorityType|AccountingStorageType|AccountingStorageTRES|AcctGatherEnergyType|TaskPlugin|GresTypes|TimeZone|SLURM_VERSION|ClusterName)"; echo; date; echo "TZ=$TZ"' | Tee-Object "$fx\00-cluster-info.txt"
```
Expected: a file with the scheduler settings (`SchedulerType=sched/backfill`), `AcctGatherEnergyType`, and the container's clock. **Do not filter out lines that surprise you.**

---

### Task 2: Give the cluster fake GPUs, a `flex` QoS, and three test users

**Files:**
- Create: `agent/tests/fixtures/slurm/01-gres-and-users.txt`

**Interfaces:**
- Consumes: `$C`, `sx`, `$fx` from Task 1.
- Produces: nodes `c1`,`c2` with 4 fake GPUs each; QoS `flex`; Slurm accounts and users `alice` (job owner), `bob` (another plain user), `wsagent` (the future agent identity, operator level); job partition name in `$P`.

- [ ] **Step 1: Look at the current configuration before changing anything**

```powershell
sx 'ls /etc/slurm; echo ---; grep -nEi "^(NodeName|PartitionName|GresTypes)" /etc/slurm/slurm.conf; echo ---; cat /etc/slurm/gres.conf 2>&1'
```
Expected: node and partition lines. Note the partition name; call it `$P` (usually `normal`).

- [ ] **Step 2: Add fake generic-resource GPUs (no real hardware needed)**

```powershell
sx 'cp /etc/slurm/slurm.conf /etc/slurm/slurm.conf.orig
grep -q "^GresTypes" /etc/slurm/slurm.conf || sed -i "1i GresTypes=gpu" /etc/slurm/slurm.conf
sed -i -E "/^NodeName=c[12]/ s/$/ Gres=gpu:4/" /etc/slurm/slurm.conf
printf "NodeName=c1 Name=gpu Count=4\nNodeName=c2 Name=gpu Count=4\n" > /etc/slurm/gres.conf
grep -nE "^(GresTypes|NodeName)" /etc/slurm/slurm.conf'
```
Expected: `GresTypes=gpu` and both node lines now end with `Gres=gpu:4`. If the config directory is mounted read-only from the host, edit the mounted copy under `$lab\config\` instead and re-run `docker compose up -d`.

- [ ] **Step 3: Copy the same config to the compute nodes and restart the daemons**

```powershell
foreach ($n in "c1","c2") {
  docker cp "${C}:/etc/slurm/slurm.conf" "$lab\slurm.conf.spike"
  docker cp "${C}:/etc/slurm/gres.conf"  "$lab\gres.conf.spike"
  docker cp "$lab\slurm.conf.spike" "${n}:/etc/slurm/slurm.conf"
  docker cp "$lab\gres.conf.spike"  "${n}:/etc/slurm/gres.conf"
}
docker compose restart slurmctld c1 c2
Start-Sleep -Seconds 20
sx 'sinfo -N -o "%N %T %G"'
```
Expected: both nodes `idle` with `gpu:4` in the GRES column. If a node shows `inval`/`drain`, run `sx 'scontrol show node c1 | grep -i reason'`, fix the mismatch, and repeat; do not continue until both nodes are `idle`.

- [ ] **Step 4: Create users on the controller and both nodes (jobs need the same users everywhere)**

```powershell
foreach ($h in $C,"c1","c2") {
  foreach ($u in "alice","bob","wsagent") { docker exec $h bash -lc "id $u >/dev/null 2>&1 || useradd -m -u $(if($u -eq 'alice'){2001}elseif($u -eq 'bob'){2002}else{2003}) $u" }
}
sx 'id alice; id bob; id wsagent'
```
Expected: the same uid for each user on all three containers (2001, 2002, 2003).

- [ ] **Step 5: Create the Slurm account, the `flex` QoS and the associations**

```powershell
sx 'sacctmgr -i add account labs Description=spike Organization=wattshift
sacctmgr -i add qos flex
sacctmgr -i add user alice Account=labs
sacctmgr -i add user bob Account=labs
sacctmgr -i add user wsagent Account=labs AdminLevel=Operator
sacctmgr -i modify user alice set qos+=flex
sacctmgr -i modify user bob set qos+=flex
sacctmgr show user alice,bob,wsagent format=User,Account,AdminLevel,QOS -P'
```
Expected: three users listed, `wsagent` with `AdminLevel=Operator`, `alice` and `bob` with `flex` in QOS. (`sacctmgr` may print "Nothing modified" for already-set values; that is fine.)

- [ ] **Step 6: Prove a GPU job can be submitted and counted, then record everything**

```powershell
put t1.sh @'
source /root/lib.sh
P=$(sinfo -h -o %P | head -1 | tr -d "*")
su alice -c "sbatch -p $P --qos=flex --gres=gpu:2 -t 2 --wrap='sleep 5'"
sleep 10
sacct -X -P -o JobID,User,QOS,AllocTRES,State | tail -3
'@
sx 'bash /root/t1.sh' | Tee-Object "$fx\01-gres-and-users.txt"
```
Expected: a `Submitted batch job` line, then a row for alice's job with `AllocTRES` containing `gres/gpu=2`. **This is the first evidence for Q4.**

---

### Task 3: Experiment A, controlling the start time (Q1)

**Files:**
- Create: `agent/tests/fixtures/slurm/02-starttime-*.txt`

**Interfaces:**
- Consumes: `$C`, `sx`, `$fx`, users, `wait_state`.
- Produces: the answer to Q1 with numbers (seconds between the set time and the real start).

- [ ] **Step 1: A pending job with a far-future begin time, then pull it forward**

```powershell
put a1.sh @'
source /root/lib.sh
P=$(sinfo -h -o %P | head -1 | tr -d "*")
echo "T0 $(stamp)"
ID=$(su alice -c "sbatch --parsable -p $P --qos=flex --gres=gpu:1 -t 5 --begin=now+3hours --wrap=\"echo started at \$(date -u +%FT%TZ); sleep 3\"")
echo "submitted job $ID"
scontrol show job $ID | grep -E "JobState|Reason|StartTime|EligibleTime"
echo "--- now set StartTime = now+45 seconds as the operator"
TARGET=$(date -u -d "+45 seconds" +%Y-%m-%dT%H:%M:%S)
su wsagent -c "scontrol update JobId=$ID StartTime=$TARGET"
echo "set StartTime=$TARGET (UTC) at $(stamp)"
scontrol show job $ID | grep -E "JobState|Reason|StartTime"
wait_state $ID RUNNING 120
squeue -j $ID -h -o "%i %T %S" 
sacct -j $ID -X -P -o JobID,Submit,Eligible,Start,State
echo "target was $TARGET"
'@
sx 'bash /root/a1.sh' | Tee-Object "$fx\02-starttime-pull-forward.txt"
```
Expected: `Reason=BeginTime` while waiting; after the update `StartTime` shows the new time; the job reaches `RUNNING` within a few seconds *after* the target. **Record the gap. Pass = it starts at or shortly after the target, never before, and never much later (under about 30 s).**

- [ ] **Step 2: Setting a start time on a job that is pending for a different reason (no free GPUs)**

Fill all 8 GPUs, submit a job that must wait, then give it a future start time:

```powershell
put a2.sh @'
source /root/lib.sh
P=$(sinfo -h -o %P | head -1 | tr -d "*")
for n in 1 2; do su alice -c "sbatch --parsable -p $P --qos=flex --gres=gpu:4 -N1 -t 3 --wrap=\"sleep 170\"" ; done
sleep 3
ID=$(su bob -c "sbatch --parsable -p $P --qos=flex --gres=gpu:2 -t 2 --wrap=\"echo bob ran at \$(date -u +%FT%TZ)\"")
echo "bob job $ID is queued behind full GPUs"
squeue -j $ID -h -o "%i %T %r"
TARGET=$(date -u -d "+240 seconds" +%Y-%m-%dT%H:%M:%S)
su wsagent -c "scontrol update JobId=$ID StartTime=$TARGET"
echo "set StartTime=$TARGET (UTC); GPUs free up in about 170 s, well before that"
sleep 5; scontrol show job $ID | grep -E "JobState|Reason|StartTime"
wait_state $ID RUNNING 300
sacct -j $ID -X -P -o JobID,Start,State
echo "target was $TARGET"
'@
sx 'bash /root/a2.sh' | Tee-Object "$fx\02-starttime-while-resources-busy.txt"
```
Expected: the job stays `PENDING` after the two GPU-holding jobs end (about 170 s) and only starts at or just after the target. **This is the key safety check: Slurm must honour the start time even when resources are free earlier.** Record the gap.

- [ ] **Step 3: Releasing early, i.e. what the `release_all` switch will do**

```powershell
put a3.sh @'
source /root/lib.sh
P=$(sinfo -h -o %P | head -1 | tr -d "*")
ID=$(su alice -c "sbatch --parsable -p $P --qos=flex --gres=gpu:1 -t 2 --begin=now+2hours --wrap=\"sleep 2\"")
scontrol show job $ID | grep -E "JobState|Reason|StartTime"
su wsagent -c "scontrol update JobId=$ID StartTime=now"
echo "released at $(stamp)"
wait_state $ID RUNNING 60
'@
sx 'bash /root/a3.sh' | Tee-Object "$fx\02-starttime-release-now.txt"
```
Expected: the job leaves `BeginTime` and runs within seconds. If `StartTime=now` is rejected, try `StartTime=now+1` and record which form works.

- [ ] **Step 4: Accepted time formats and edge cases**

```powershell
put a4.sh @'
P=$(sinfo -h -o %P | head -1 | tr -d "*")
ID=$(su alice -c "sbatch --parsable -p $P --qos=flex -t 2 --begin=now+2hours --wrap=\"sleep 1\"")
for T in "2099-01-01T00:00:00" "now+90" "now+5minutes" "2020-01-01T00:00:00" "garbage"; do
  echo "== StartTime=$T"; su wsagent -c "scontrol update JobId=$ID StartTime=$T" 2>&1; scontrol show job $ID | grep -o "StartTime=[^ ]*"
done
scancel $ID
'@
sx 'bash /root/a4.sh' | Tee-Object "$fx\02-starttime-formats.txt"
```
Expected: which absolute and relative forms are accepted, what happens for a time in the past, and that garbage is rejected with an error message. **The agent will always send absolute UTC `YYYY-MM-DDTHH:MM:SS`; confirm that form works and note whether it is read as UTC (the container clock is UTC).**

- [ ] **Step 5: Write the Q1 verdict**

Add to a scratch note (kept for Task 7): the measured gaps from Steps 1-3 (seconds after target), which forms are accepted, and one sentence: `Q1: PASS/FAIL, because ...`. **If FAIL, stop and tell the user before continuing: the whole control mechanism changes.**

---

### Task 4: Experiment B, predicted start as a baseline (Q2)

**Files:**
- Create: `agent/tests/fixtures/slurm/03-predicted-start-*.txt`

- [ ] **Step 1: Predicted start on a busy cluster**

```powershell
put b1.sh @'
P=$(sinfo -h -o %P | head -1 | tr -d "*")
for n in 1 2; do su alice -c "sbatch --parsable -p $P --qos=flex --gres=gpu:4 -N1 -t 4 --wrap=\"sleep 230\"" ; done
sleep 3
ID=$(su bob -c "sbatch --parsable -p $P --qos=flex --gres=gpu:2 -t 3 --wrap=\"sleep 1\"")
echo "pending job $ID"
squeue -j $ID -h -o "%i %T %r start=%S"
squeue --start -j $ID -o "%i %T %S %r"
scontrol show job $ID | grep -E "StartTime|SchedNodeList|Reason"
echo "--- same, but the job has NO time limit set (most users omit it)"
ID2=$(su bob -c "sbatch --parsable -p $P --qos=flex --gres=gpu:2 --wrap=\"sleep 1\"")
squeue --start -j $ID2 -o "%i %T %S %r"
'@
sx 'bash /root/b1.sh' | Tee-Object "$fx\03-predicted-start-busy.txt"
```
Expected: for a job pending on resources, a predicted `START_TIME` (a real timestamp) once the backfill scheduler has run, or `N/A` if it has not yet or cannot predict. Wait up to a minute and re-run the `squeue --start` lines once if you see `N/A`.

- [ ] **Step 2: How often is it `N/A`?**

Repeat the query every 10 seconds for 90 seconds and count:

```powershell
sx 'ID=$(squeue -u bob -h -o %i | head -1); for i in 1 2 3 4 5 6 7 8 9; do echo "$(date -u +%T) $(squeue --start -j $ID -h -o "%S")"; sleep 10; done' | Tee-Object "$fx\03-predicted-start-stability.txt"
```
Expected: the value is a timestamp most of the time, and does not jump wildly. **Decision rule for Q2: if a real timestamp is present for pending-on-resources jobs in at least 8 of 9 samples, use it as `baseline_start` (fall back to submit time when `N/A`). If it is often `N/A` or unstable, the design uses submit time as the baseline and says so.**

- [ ] **Step 3: Q2 verdict** in the scratch note: percentage available, whether the value drifts, and the decision.

---

### Task 5: Experiment C, accounting fields, GPU counts, energy, and machine-readable times (Q3, Q4, Q5, Q6)

**Files:**
- Create: `agent/tests/fixtures/slurm/04-accounting-*.txt`, `05-machine-readable-*.txt`

- [ ] **Step 1: A completed job's full accounting record (Q3, Q4)**

```powershell
put c1.sh @'
source /root/lib.sh
P=$(sinfo -h -o %P | head -1 | tr -d "*")
ID=$(su alice -c "sbatch --parsable -p $P --qos=flex --gres=gpu:3 -t 5 -J train-demo --wrap=\"sleep 20\"")
wait_state $ID COMPLETED 90
sacct -j $ID -X -P -o JobID,JobName,User,Partition,QOS,Submit,Eligible,Start,End,Elapsed,ElapsedRaw,TimelimitRaw,ReqTRES,AllocTRES,State,ExitCode,Comment
echo "--- with steps (no -X) to see whether GPU info also appears per step"
sacct -j $ID -P -o JobID,AllocTRES,State
echo "--- the fields sacct can print (for reference)"
sacct --helpformat | tr -s " " "\n" | grep -Ei "energy|gres|tres|timelimit|start|end|submit|eligible" | sort -u | tr "\n" " "
'@
sx 'bash /root/c1.sh' | Tee-Object "$fx\04-accounting-completed.txt"
```
Expected: `AllocTRES` contains `gres/gpu=3`; `ReqTRES` shows what was requested; `ElapsedRaw` is seconds; `State=COMPLETED`. **Note exactly which fields are populated and which are empty.**

- [ ] **Step 2: Failed, cancelled and timed-out states**

```powershell
put c2.sh @'
source /root/lib.sh
P=$(sinfo -h -o %P | head -1 | tr -d "*")
F=$(su alice -c "sbatch --parsable -p $P --qos=flex -t 2 --wrap=\"exit 3\"")
C=$(su alice -c "sbatch --parsable -p $P --qos=flex -t 5 --begin=now+1hour --wrap=\"sleep 1\"")
T=$(su alice -c "sbatch --parsable -p $P --qos=flex -t 1 --wrap=\"sleep 300\"")
scancel $C
wait_state $F FAILED 60
wait_state $T TIMEOUT 150
sacct -j $F,$C,$T -X -P -o JobID,State,ExitCode,Start,End,Elapsed
'@
sx 'bash /root/c2.sh' | Tee-Object "$fx\04-accounting-terminal-states.txt"
```
Expected: `FAILED` with a non-zero exit code, `CANCELLED` (a job cancelled while pending has `Start=Unknown`), and `TIMEOUT`. **These are the states the agent must map.**

- [ ] **Step 3: Energy (Q5)**

```powershell
sx 'scontrol show config | grep -Ei "AcctGatherEnergy|AcctGatherProfile|JobAcctGather"; echo ---; sacct -j 1 -X -P -o JobID,ConsumedEnergy,ConsumedEnergyRaw'
```
Save with `Tee-Object "$fx\04-accounting-energy.txt"`. Expected: `AcctGatherEnergyType=acct_gather_energy/none` (no energy plugin in a container) and empty energy columns. **Q5 verdict: energy is not testable here; record that the field names exist (`ConsumedEnergyRaw`) and that measured power is deferred, as the spec says.**

- [ ] **Step 4: Time formats and JSON (Q6)**

```powershell
sx 'ID=$(sacct -X -n -P -o JobID | tail -1)
echo "default:"; sacct -j $ID -X -P -o Start,End
echo "epoch:";   SLURM_TIME_FORMAT=%s sacct -j $ID -X -P -o Start,End
echo "squeue epoch:"; SLURM_TIME_FORMAT=%s squeue -h -o "%i %S" | head -3
echo "scontrol epoch:"; SLURM_TIME_FORMAT=%s scontrol show job $ID 2>&1 | grep -oE "(Submit|Start|End)Time=[^ ]*"
echo "json?"; squeue --json 2>&1 | head -c 400; echo; sacct -j $ID --json 2>&1 | head -c 400; echo
echo "TZ: $(date +%Z), offset $(date +%z)"' | Tee-Object "$fx\05-machine-readable-times.txt"
```
Expected: default times print without a zone (local time of the controller), the `SLURM_TIME_FORMAT=%s` forms print epoch seconds, and `--json` either works (printing an object) or errors. **Decision rule for Q6: prefer epoch seconds via `SLURM_TIME_FORMAT=%s` with `-P` (pipe-delimited) output; use `--json` only if it worked everywhere it is needed. Record which command forms work for each of `squeue`, `sacct`, `scontrol`.**

- [ ] **Step 5: Verdicts for Q3-Q6** in the scratch note: the exact `sacct` field list the reporter will use, how `gpus` is derived (`gres/gpu=N` from `AllocTRES`, or `ReqTRES` while pending), energy status, and the chosen time-parsing method.

---

### Task 6: Experiment D, resilience, users' own changes, privileges and special jobs (Q7, Q8, Q9)

**Files:**
- Create: `agent/tests/fixtures/slurm/06-*.txt`

- [ ] **Step 1: Does a start time survive a controller restart? (Q7)**

```powershell
put d1.sh @'
source /root/lib.sh
P=$(sinfo -h -o %P | head -1 | tr -d "*")
ID=$(su alice -c "sbatch --parsable -p $P --qos=flex --gres=gpu:1 -t 2 --begin=now+2hours --wrap=\"echo ran at \$(date -u +%FT%TZ)\"")
TARGET=$(date -u -d "+150 seconds" +%Y-%m-%dT%H:%M:%S)
su wsagent -c "scontrol update JobId=$ID StartTime=$TARGET"
echo "$ID $TARGET" > /root/d1.state
scontrol show job $ID | grep -E "JobState|Reason|StartTime"
'@
sx 'bash /root/d1.sh' | Tee-Object "$fx\06-restart-before.txt"
docker restart $C
Start-Sleep -Seconds 25
sx 'read ID TARGET < /root/d1.state; source /root/lib.sh; echo "after restart, $(stamp):"; scontrol show job $ID | grep -E "JobState|Reason|StartTime"; wait_state $ID RUNNING 200; sacct -j $ID -X -P -o JobID,Start,State; echo "target was $TARGET"' | Tee-Object "$fx\06-restart-after.txt"
```
Expected: after the restart the job is still `PENDING` with the same `StartTime`, and it starts at the target. **This is the evidence for "if our software dies, jobs still start".** Note: this restarts only the controller; the agent does not exist yet, so "agent down" is trivially true because Slurm alone enforces the time.

- [ ] **Step 2: What the user's own actions look like (Q8)**

```powershell
put d2.sh @'
P=$(sinfo -h -o %P | head -1 | tr -d "*")
mk() { su alice -c "sbatch --parsable -p $P --qos=flex -t 5 --begin=now+2hours --wrap=\"sleep 1\""; }
show() { scontrol show job $1 | grep -oE "JobState=[^ ]*|Reason=[^ ]*|StartTime=[^ ]*|Comment=[^ ]*" | tr "\n" " "; echo; }

A=$(mk); su wsagent -c "scontrol update JobId=$A StartTime=2099-01-01T00:00:00 Comment=wattshift:managed"
echo "== managed job before"; show $A
echo "== alice (owner) changes StartTime herself"; su alice -c "scontrol update JobId=$A StartTime=now+10minutes" 2>&1; show $A
echo "== alice holds it";   su alice -c "scontrol hold $A" 2>&1; show $A
echo "== alice releases it"; su alice -c "scontrol release $A" 2>&1; show $A
echo "== bob (not the owner) tries to change it"; su bob -c "scontrol update JobId=$A StartTime=now" 2>&1
echo "== alice cancels it"; su alice -c "scancel $A"; sleep 1; show $A; sacct -j $A -X -P -o JobID,State,Start,Comment
'@
sx 'bash /root/d2.sh' | Tee-Object "$fx\06-user-overrides.txt"
```
Expected: shows (a) whether an owner may change their own job's `StartTime`, (b) that a hold appears as `Reason=JobHeldUser` and a changed `StartTime` differs from the value the agent set, (c) that a non-owner is refused, (d) whether `Comment` is stored and readable by `scontrol` and `sacct`. **Decision rule for Q8: the agent records the `StartTime` it applied; on the next poll, if the job's `StartTime` or state differs from what was applied (or the `Comment` marker is gone), stop managing that job. If `Comment` cannot be set or read, the agent keeps its own local record of applied jobs instead.**

- [ ] **Step 3: Which privilege does the agent need? (Q9)**

```powershell
put d3.sh @'
P=$(sinfo -h -o %P | head -1 | tr -d "*")
ID=$(su alice -c "sbatch --parsable -p $P --qos=flex -t 5 --begin=now+2hours --wrap=\"sleep 1\"")
echo "== operator wsagent updates alice job StartTime"; su wsagent -c "scontrol update JobId=$ID StartTime=now+1hour" 2>&1
echo "== operator lists everyone pending"; su wsagent -c "squeue -t PD -h -o \"%i %u %T\"" 2>&1 | head -3
echo "== operator reads accounting for others"; su wsagent -c "sacct -a -X -P -o JobID,User,State | head -3" 2>&1
echo "== operator tries to scancel (must be allowed or not; note which)"; su wsagent -c "scancel $ID" 2>&1
echo "== plain user bob tries to list alice pending jobs"; su bob -c "squeue -t PD -h -o \"%i %u\"" 2>&1 | head -3
echo "== read-only identity for shadow mode: does plain bob see other users jobs in sacct?"; su bob -c "sacct -a -X -P -o JobID,User | head -3" 2>&1
'@
sx 'bash /root/d3.sh' | Tee-Object "$fx\06-privileges.txt"
```
Expected: `wsagent` (Operator) can update `StartTime` on another user's job; whether Operator can also `scancel` is recorded (the agent must never need it). Note what a plain user can read, because **shadow mode needs only read access**. **Verdict: the smallest Slurm level that works for autonomous mode, and whether a read-only identity can see all users' pending jobs and accounting (`PrivateData` settings may hide them).**

- [ ] **Step 4: Arrays, dependencies and requeues (Q9)**

```powershell
put d4.sh @'
source /root/lib.sh
P=$(sinfo -h -o %P | head -1 | tr -d "*")
echo "== array"; A=$(su alice -c "sbatch --parsable -p $P --qos=flex -t 2 --array=1-3 --begin=now+2hours --wrap=\"sleep 1\"")
squeue -j ${A%%;*} -h -o "%i|%K|%T|%r" ; scontrol show job ${A%%;*} | grep -oE "JobId=[^ ]*|ArrayJobId=[^ ]*|ArrayTaskId=[^ ]*" | head -4
echo "== dependency"; B=$(su alice -c "sbatch --parsable -p $P --qos=flex -t 2 --wrap=\"sleep 20\"")
D=$(su alice -c "sbatch --parsable -p $P --qos=flex -t 2 --dependency=afterok:$B --wrap=\"sleep 1\"")
squeue -j $D -h -o "%i|%T|%r|%E"
echo "== requeue"; R=$(su alice -c "sbatch --parsable -p $P --qos=flex -t 5 --requeue --wrap=\"sleep 60\"")
wait_state $R RUNNING 30; scontrol requeue $R; sleep 3
scontrol show job $R | grep -oE "JobState=[^ ]*|Restarts=[^ ]*|Reason=[^ ]*"
scancel ${A%%;*} $D $R
'@
sx 'bash /root/d4.sh' | Tee-Object "$fx\06-special-jobs.txt"
```
Expected: an array shows one line with a bracketed task range (`123_[1-3]`), a dependent job shows `Reason=Dependency` with the dependency text, and a requeued job shows `Restarts=1` and pending again. **Verdict for v1: the agent recognises these by (array id present, dependency present, restarts>0) and skips them, as the spec says. Record the exact `squeue` format strings that reveal each case.**

---

### Task 7: Write the findings, correct the spec, save the fixtures, tear down

**Files:**
- Create: `docs/superpowers/spikes/2026-09-19-slurm-spike-findings.md`
- Modify: `docs/superpowers/specs/2026-09-19-slurm-agent-automation-design.md` (section 16, plus any section a finding contradicts)

- [ ] **Step 1: Write the findings document**

Create the file with this exact structure and fill every cell from the scratch notes (a blank or "not tested" cell must say so and why):

```markdown
# Slurm spike 0 findings (2026-09-19)

Environment: Slurm <version from 00-cluster-info.txt>, image `giovtorres/slurm-docker-cluster:latest` (<digest>), 2 nodes x 4 fake GPUs, backfill scheduler, accounting via slurmdbd.

## Answers

| # | Question | Verdict | Evidence | What changes in the design |
|---|---|---|---|---|
| Q1 | StartTime control works and is honoured on time, even when resources are free | PASS/FAIL | fixtures/slurm/02-*.txt | ... |
| Q2 | Predicted start usable as baseline | ... | 03-*.txt | ... |
| Q3 | sacct fields for real start/end/state | ... | 04-*.txt | exact field list |
| Q4 | GPU counting | ... | 01-*.txt, 04-*.txt | ... |
| Q5 | Per-job energy | not testable in a container | 04-accounting-energy.txt | deferred, as in the spec |
| Q6 | Unambiguous times and JSON | ... | 05-*.txt | parsing method |
| Q7 | Survives controller restart | ... | 06-restart-*.txt | ... |
| Q8 | User overrides and our marker | ... | 06-user-overrides.txt | ... |
| Q9 | Privilege; arrays, dependencies, requeues | ... | 06-privileges.txt, 06-special-jobs.txt | ... |

## Measured start accuracy

Seconds between the set StartTime and the real start, for each Task 3 run: <numbers>.

## Design changes required

<a numbered list; write "none" only if every verdict is PASS and nothing surprised us>

## Limits of this test

One controller, two nodes, fake GPUs, no energy plugin, no real workload, default scheduler settings. A real cluster may
behave differently under priority weights, preemption or partition rules; the first real customer must repeat Q1 and Q7.

## Recorded fixtures

<a list of every file under agent/tests/fixtures/slurm/ with one line each>
```

- [ ] **Step 2: Correct the spec**

In `docs/superpowers/specs/2026-09-19-slurm-agent-automation-design.md`:
1. Replace the "Spike 0" bullet list in section 16 with a "Verified" list quoting each verdict and pointing to the findings file.
2. For every "Design changes required" item, edit the section that made the wrong assumption (sections 4, 6, 7, 8 or 10) so the spec no longer contradicts the evidence.
3. Change the top status line to: `Status: approved; Spike 0 done on <date>, see findings.`

Check there are no leftover contradictions:

```powershell
Select-String -Path "C:\Users\aakur\OneDrive\Desktop\WattShift\docs\superpowers\specs\2026-09-19-slurm-agent-automation-design.md" -Pattern "verify|to verify|assum|unverified" -CaseSensitive:$false
```
Expected: any remaining hits are deliberate (risks section) and not claims the spike already settled.

- [ ] **Step 3: Confirm the fixtures are complete and readable**

```powershell
Get-ChildItem "C:\Users\aakur\OneDrive\Desktop\WattShift\agent\tests\fixtures\slurm" | Select-Object Name, @{n='bytes';e={$_.Length}}
```
Expected: 00-cluster-info, 01-gres-and-users, 02-starttime-* (4 files), 03-predicted-start-* (2), 04-accounting-* (4), 05-machine-readable-times, 06-* (6). **No file may be empty.** Open any empty one and re-run its step.

- [ ] **Step 4: Tear down (ask before deleting images)**

```powershell
Set-Location $lab
docker compose down -v
docker ps -a --format '{{.Names}}'
```
Expected: no Slurm containers remain. Ask the user whether to also remove the pulled images (`docker image rm giovtorres/slurm-docker-cluster:latest` and the MySQL image) to free disk; do it only if they say yes. Keep the `$lab` clone until the user confirms the findings are accepted, because Plan 5 (end-to-end proof) reuses it.

- [ ] **Step 5: Report to the user**

Say, in plain words: which of Q1-Q9 passed, the measured start accuracy, whether the design needs to change, and what the next plan (cloud core) can now assume. **Do not commit unless the user asks.**

---

## Self-review (done when this plan was written)

**Spec coverage.** Section 16's four questions map to Q1 (StartTime under backfill and priority), Q2 (predicted start), Q3-Q4 (`sacct` fields, GPU counting), plus Q5-Q9 added because sections 7, 8 and 10 depend on them (energy, time formats, restart survival, user overrides, privileges and special jobs). Sections 4-6 and 11-14 are not in scope for a spike and get their own plans (cloud core, agent, dashboard).

**Placeholders.** The only angle-bracket items are values that cannot be known until the run (`<scratchpad>` path, Slurm version, digest, measured seconds), and each is filled by a specific step. Expected outputs are hypotheses to check, and every experiment has a stated decision rule for what to do if reality differs.

**Consistency.** Names used across tasks are defined once in Task 1 and Task 2: `$C`, `$lab`, `$fx`, `$P` (the partition, computed inline as `P=$(sinfo -h -o %P | head -1 | tr -d "*")` in each script so scripts are self-contained), `sx`, `wait_state`, `stamp`, users `alice`, `bob`, `wsagent`, QoS `flex`. Fixture filenames in Task 7 match the ones created in Tasks 1-6.
