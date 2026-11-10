"""
progress.py — turn a GVHMR run log (<clip>.run.log) into a one-line progress bar.

  python3 infra/aws/progress.py run.log [--since EPOCH] [--done]
                                # one line, for a log on disk (--done: the BVH has landed)
  python3 infra/aws/progress.py --watch BUCKET CLIP [--since EPOCH] [--every 10]
                                # follow s3://BUCKET/CLIP.run.log until DONE (or the box dies)

launch.sh calls this while it watches a run. Standard library only (the log is
fetched with the AWS CLI), so it runs on any machine with python3.

The bar is weighted by how long each stage takes on a g5.xlarge (minutes, from
real runs), so it moves at roughly a steady pace. Install and checkpoint stages
have no percentages of their own; they advance by which command the log has
reached. The GVHMR stages read the tqdm percentages straight out of the log.
"""
import re
import subprocess
import sys
import time

# (name, minutes, markers that mean this stage has begun) — in run order.
# Minutes from a timed g5.xlarge run on a 29.5s clip (2026-11-10: inference
# started 5m30s in, preprocessing took 139s, 9m33s launch to BVH).
STAGES = [
    ("booting",                1.0, []),
    ("installing system deps", 0.6, [r"^\+ (\[[\d:]+\] )?apt-get update"]),
    ("cloning GVHMR",          0.3, [r"^\+ (\[[\d:]+\] )?git clone"]),
    ("installing torch",       1.2, [r"pip install torch=="]),
    ("installing GVHMR deps",  1.6, [r"pip install --no-build-isolation chumpy",
                                     r"pip install -r requirements.txt"]),
    ("fetching checkpoints",   0.8, [r"^\+ (\[[\d:]+\] )?mkdir -p inputs/checkpoints",
                                     r"Using cached checkpoints"]),
    ("loading the clip",       1.2, [r"SMPLX_NEUTRAL.npz inputs/checkpoints"]),
    ("tracking people (YOLO)", 0.8, [r"\[Preprocess\] Start", r"YoloV8 Tracking"]),
    ("2D pose (ViTPose)",      0.6, [r"ViTPose:"]),
    ("image features (HMR2)",  0.9, [r"HMR2 Feature:"]),
    ("solving motion (GVHMR)", 0.2, [r"\[HMR4D\] Predicting"]),
    ("writing BVH",            0.2, [r"\[HMR4D\] Elapsed", r"^\+ (\[[\d:]+\] )?aws s3 cp \S+\.bvh"]),
]
TQDM = {7: "YoloV8 Tracking", 8: "ViTPose", 9: "HMR2 Feature"}
DONE = r"^DONE: "
WIDTH = 28


def parse(text):
    """-> (overall 0-1, stage name, detail) for the log so far."""
    lines = text.replace("\r", "\n").splitlines()
    if any(re.search(DONE, l) for l in lines):
        return 1.0, "done", ""
    stage = 0
    for line in lines:
        for i, (_, _, marks) in enumerate(STAGES):
            if i > stage and any(re.search(m, line) for m in marks):
                stage = i
    frac, detail = 0.0, ""
    if stage in TQDM:
        pcts = [int(m) for m in re.findall(re.escape(TQDM[stage]) + r":\s+(\d+)%", text)]
        if pcts:
            frac, detail = pcts[-1] / 100, f"{pcts[-1]}%"
    weights = [w for _, w, _ in STAGES]
    total = sum(weights)
    overall = (sum(weights[:stage]) + weights[stage] * frac) / total
    return min(overall, 0.99), STAGES[stage][0], detail


def fmt_secs(s):
    s = int(max(0, s))
    return f"{s // 60}m{s % 60:02d}s"


def line(overall, name, detail, since=None):
    filled = int(round(overall * WIDTH))
    bar = "#" * filled + "-" * (WIDTH - filled)
    out = f"[{bar}] {overall * 100:3.0f}%  {name}" + (f" {detail}" if detail else "")
    if since:
        el = time.time() - since
        out += f"  · {fmt_secs(el)} elapsed"
        if 0.08 < overall < 1:
            out += f" · ~{fmt_secs(el / overall * (1 - overall))} left"
    return out


def fetch(bucket, clip):
    r = subprocess.run(["aws", "s3", "cp", f"s3://{bucket}/{clip}.run.log", "-"],
                       capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


def main(argv):
    since = float(argv[argv.index("--since") + 1]) if "--since" in argv else None
    if "--watch" in argv:
        bucket, clip = argv[argv.index("--watch") + 1], argv[argv.index("--watch") + 2]
        every = float(argv[argv.index("--every") + 1]) if "--every" in argv else 10
        while True:
            overall, name, detail = parse(fetch(bucket, clip))
            sys.stdout.write("\r\033[K" + line(overall, name, detail, since))
            sys.stdout.flush()
            if overall >= 1:
                print()
                return
            time.sleep(every)
    text = open(argv[0], errors="replace").read()
    # The BVH can land before the heartbeat has uploaded the log's DONE line.
    print(line(1.0, "done", "", since) if "--done" in argv else line(*parse(text), since))


if __name__ == "__main__":
    main(sys.argv[1:])
