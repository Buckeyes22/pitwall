# B2 — Install your tools and check your clone

**Mode:** training | **Needs:** B1 | **Output:** evidence files

## Goal

A complete toolchain and a working Pitwall environment.

## You will learn

- Package managers.
- What each tool is for.
- The Docker group.
- `uv` and the project environment.

## Before you start

The kickoff already installed `git` and `gh` and cloned the repository. This is the only lesson where `sudo` is allowed (rule R2); the tester types every `sudo` and password, and the coach never runs `sudo`.

## Steps

### Step 1 — Which Linux

Coach: the right package manager depends on the distribution. Tester does:

```bash
grep -E '^(ID|ID_LIKE|VERSION_ID)=' /etc/os-release
```

Expected: lines such as `ID=ubuntu`. Use `apt` for ubuntu/debian, `dnf` for fedora/rhel, `pacman` for arch. Record the distribution on the "Machine" line.

### Step 2 — What's installed

Coach: each command prints a version when present; `command not found` means install it next. Tester does:

```bash
git --version; gh --version; curl --version | head -1; make --version | head -1; ss -V
script --version; docker --version; docker compose version; uv --version
```

Expected: each prints a version line.

### Step 3 — Small tools, only if missing

Coach: pick the install line for the tester's package manager; the tester types the `sudo`. Tester does (pick one):

```bash
sudo apt update && sudo apt install -y curl make iproute2 util-linux
sudo dnf install -y curl make iproute util-linux
sudo pacman -S --needed curl make iproute2 util-linux
```

Expected: no errors, and the version checks from Step 2 now pass.

### Step 4 — Docker Engine, only if missing

Coach: Docker's own quick-install script for development machines; `sudo sh` runs a downloaded script as the administrator, so the URL must come from Docker. Log out and back in for the group change to take effect. Tester does:

```bash
curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
sudo sh /tmp/get-docker.sh
sudo usermod -aG docker "$USER"
docker run --rm hello-world
```

Expected: the output contains `Hello from Docker!`.
If different: `permission denied` on `docker.sock` means the group change is not active yet. Log out and in again, or reboot.

### Step 5 — uv, only if missing

Coach: `uv` is the project Python tool; the installer writes to `~/.local/bin` (open a new terminal before `uv --version`). Tester does:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv --version
```

Expected: `uv 0.` followed by numbers.

### Step 6 — GitHub access

Coach: the tester must be a collaborator on the repo for these checks to pass. Tester does:

```bash
gh auth status
gh repo view Buckeyes22/pitwall --json visibility --jq .visibility
```

Expected: `Logged in to github.com`, then `PRIVATE` or `PUBLIC`.
If different: the collaborator invitation has not been accepted. Ask the maintainer.

### Step 7 — The working clone

Coach: these checks cover remote, branch state, and `.gitignore` for the progress file. Tester does:

```bash
git remote -v; git status; git check-ignore qa/.work/progress.md
```

Expected: `origin` points at `github.com/Buckeyes22/pitwall`; `On branch main` and a clean tree; the progress path is printed, which proves git ignores it.

### Step 8 — The project environment

Coach: `uv sync` downloads Python 3.14.7 and the exact packages into `.venv`; the first run takes several minutes. Tester does:

```bash
uv sync --frozen --extra dev 2>&1 | tail -3; ls -d .venv
```

Expected: no error, then `.venv`.

### Step 9 — First Pitwall commands

Coach: `--version` and `--help` are always safe; bare `pitwall` is not (rule R2). Tester does:

```bash
uv run pitwall --version; uv run pitwall --help 2>&1 | head -3
```

Expected: a version such as `0.1.0a2`, then a first line starting `Usage: pitwall`.

### Step 10 — Save the evidence

Coach: the brace group runs each version check; `tee` saves the combined output. Tester does:

```bash
{ git --version; gh --version | head -1; docker --version; docker compose version; uv --version; uv run pitwall --version; } 2>&1 | tee qa/.work/evidence/B2-versions.txt
```

Expected: six version lines saved to `B2-versions.txt`.

## Checkpoint

Ask: "What is Docker for in this project? What does `uv sync` do?"
Expected: Docker runs the test database and Redis; `uv sync` installs the exact packages into `.venv`.

## Done when

Every tool prints a version, `hello-world` works, `uv run pitwall --help` prints usage, and `B2-versions.txt` exists.

## Record in progress

Add a `Completed` row for B2. Fill in the "Machine" line. Set `Current item: B3`. Write a short session log entry.

## Next

[B3 — Fresh-eyes README test](B3-fresh-eyes-readme.md)
