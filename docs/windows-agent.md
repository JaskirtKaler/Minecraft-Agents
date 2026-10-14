# Windows desktop: join the home Minecraft server

For the packaged agent, follow [Docker on Windows](docker-agent.md): install Docker Desktop, configure `docker.env`, and run `docker compose up -d --build`. The image includes the Python/Node dependencies, and a named volume retains memory. You can use this from PowerShell; a separate Ubuntu/Conda development setup is optional.

The instructions below describe running the agent source directly with Conda, which remains useful for local development.

Use **WSL 2 → Ubuntu 24.04 or newer → Linux Conda → minecraft-agent environment**. The pinned FalkorDB Lite Linux wheels require glibc 2.39 or newer. The current launcher is Bash, and the Python graph-memory adapter uses Unix file locking (`fcntl`) and an embedded FalkorDB/Redis runtime. Native Windows Conda alone does not make this implementation compatible. WSL supplies the Linux environment those components expect. Conda keeps the project's Python and Node dependencies in a separate environment.

The desktop runs its own bot and controller and connects to Minecraft on the Mac. Its AI model service is configured separately. The YAML does not install model weights, a GPU inference runtime, Java, or a second Minecraft server. No Docker installation is needed.

## 1. Install WSL 2 and Ubuntu

In **Windows PowerShell as administrator**, run:

```powershell
wsl --install -d Ubuntu
```

Restart if prompted, then open Ubuntu and create the Linux username/password. Check `wsl --list --verbose` in PowerShell; the Ubuntu distribution should show version 2. Microsoft documents [WSL installation](https://learn.microsoft.com/en-us/windows/wsl/install) and [the WSL commands](https://learn.microsoft.com/en-us/windows/wsl/reference).

## 2. Install Conda inside Ubuntu

Use the **Linux Miniforge installer**, selected for the desktop's architecture, from [the official conda-forge download page](https://conda-forge.org/download/). Run it inside the Ubuntu terminal and enable Bash initialization, then reopen Ubuntu. The [Miniforge WSL instructions](https://github.com/conda-forge/miniforge#windows-subsystem-for-linux-wsl) specify a Linux terminal and Linux installer. A Windows Conda installation is separate and will not supply this Linux environment.

Clone the updated repository from the Ubuntu terminal into a directory such as `~/projects/Minecraft-Agents`. Microsoft recommends keeping projects in the [Linux filesystem when using Linux tools](https://learn.microsoft.com/en-us/windows/wsl/filesystems#file-storage-and-performance-across-file-systems), rather than working under `/mnt/c/`.

## 3. Install the agent dependencies

From the cloned repository root, in Ubuntu:

```bash
conda env create -f environment.yml
conda activate minecraft-agent
npm ci --prefix bot
```

The YAML sets Python 3.12 and Node.js 22 (including npm), then installs the Python packages from `requirements.txt`. The Node packages are installed from the committed `bot/package-lock.json`. Run `conda activate minecraft-agent` whenever opening a new Ubuntu terminal to launch the agent. The launcher respects the activated Conda interpreter. Run `which python` and `which node` if troubleshooting; both should resolve inside the Linux Conda environment.

If `npm ci` reports that it must compile `canvas` and is missing native libraries, install the Ubuntu prerequisites listed by [node-canvas](https://github.com/Automattic/node-canvas#compiling), then retry:

```bash
sudo apt-get update
sudo apt-get install build-essential pkg-config libcairo2-dev libpango1.0-dev libjpeg-dev libgif-dev librsvg2-dev
npm ci --prefix bot
```

For a fresh installation, create the private configuration:

```bash
cp .env.example .env
```

Preserve an existing `.env`. Set the Minecraft connection fields to:

```dotenv
MC_HOST=192.168.50.63
MC_PORT=25565
MC_USERNAME=AI_Agent_2
MC_VERSION=1.20.1
MC_WORLD_ID=localhost:25565
MC_COMMAND_PLAYERS=Pilot6117
WS_HOST=127.0.0.1
WS_PORT=8765
```

Use the Mac's current printed LAN IP if it changes. `MC_WORLD_ID=localhost:25565` is this host's current memory identity, and must match the Mac's value. Every bot needs a unique `MC_USERNAME`. The agent launcher supplies its own local `WS_URL` and separate memory/learning folders.

Also configure the model endpoints and model names in `.env` for the desktop's chosen inference service. The example uses local Ollama and is not a preset for the new Nemotron MLX service. Start the chosen model service before launching the agent. In WSL, `localhost` refers to the Linux environment unless Windows/WSL networking explicitly provides access to the Windows host; for a model running as a native Windows service, follow [Microsoft's networking instructions](https://learn.microsoft.com/en-us/windows/wsl/networking) and configure a reachable endpoint.

## 4. Join the existing server

Start Minecraft on the Mac using its normal host launcher. In the desktop's Ubuntu terminal, with the Conda environment active, run:

```bash
./start-agent.sh --host 192.168.50.63 --name AI_Agent_2 --check
./start-agent.sh --host 192.168.50.63 --name AI_Agent_2
```

The first command checks dependencies, server reachability, and the local controller port. The second starts only the bot and controller. Ctrl+C stops this desktop agent. Its logs are saved under `data/run/`; Minecraft continues running on the Mac.

In Minecraft chat, use `@AI_Agent_2 status`, `@AI_Agent_2 inventory`, or an addressed objective. Only human names in `MC_COMMAND_PLAYERS` may issue commands. The desktop does not need any router port forwarding to join the Mac on the home network.

The environment file is a portable setup specification with pinned direct Python dependencies, not an export of the Mac's installation path. Windows/WSL installation and model inference still need validation on the desktop itself.
