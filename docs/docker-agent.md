# Run an additional Minecraft agent with Docker

This image contains one Python controller and its Mineflayer bot. It connects to the Minecraft world on the Mac and an independently configured model API. Python, Node, and the project packages are installed in the image. The desktop needs Docker Desktop; Conda and a separate Ubuntu development environment are optional.

## First setup on the Windows desktop

Install [Docker Desktop for Windows](https://docs.docker.com/desktop/setup/install/windows-install/), select Linux containers with its WSL 2 backend, and start Docker Desktop. Clone the updated repository and open PowerShell in the repository folder.

Create the private Docker configuration once:

```powershell
Copy-Item docker.env.example docker.env
notepad docker.env
```

Set these values before starting:

- `MC_HOST`: the Mac's current LAN address, currently `192.168.50.63`.
- `MC_USERNAME`: a unique bot name, such as `AI_Agent_2`.
- `MC_WORLD_ID`: the host's stable world identity, currently `localhost:25565`.
- `MC_COMMAND_PLAYERS`: human usernames allowed to issue addressed commands, initially `Pilot6117`.
- `NEBIUS_BASE_URL`: the external model's OpenAI-compatible API URL, including `/v1`.
- `NEBIUS_MODEL`: the exact model ID served by that API.
- `NEBIUS_API_KEY`: the real key if that service requires one, or a nonempty placeholder for a local service that ignores keys.

The sample port `8000` is an example, and the template model names must be replaced. These environment names retain the project's Nebius terminology; normal model-led planning can use another compatible API. No model service or weights are included or started in this container.

For a model service on the Windows desktop itself, use `host.docker.internal` rather than `localhost`, for example `http://host.docker.internal:8000/v1`. Docker documents [connections from a container to a host service](https://docs.docker.com/desktop/features/networking/networking-how-tos/). For a model service on another home computer, use that computer's LAN IP and port. The chosen service must be reachable from the container. This does not configure or validate the new Nemotron MLX backend automatically.

`GRAPHITI_ENABLED=false` initially keeps exact world checkpoints and saved experience without making background graph-extraction model calls. To enable the optional graph, configure its model and embedding API fields in `docker.env`, then set `GRAPHITI_ENABLED=true`; graph files use the same persistent data volume. The legacy baseline planner's optional Jarvis advisor uses an Ollama-native `/api/chat` endpoint, separately from the normal model-led planner.

Start the Minecraft server on the Mac. On the Windows desktop:

```powershell
docker compose config --quiet
docker compose up -d --build
docker compose ps
docker compose logs --tail 100 agent
```

The first build downloads the base images and dependencies. `config --quiet` checks the configuration without printing credentials. The agent health check becomes ready only after both the bot and controller have joined and are running. If the shared server is unavailable, the container exits and Docker's restart policy retries; `docker compose stop` stops those retries intentionally. No host ports are published: the bot connects out to the game server, and its controller connection stays inside the container.

In Minecraft, send `@AI_Agent_2 status`, `@AI_Agent_2 inventory`, or an objective addressed to that bot. Plain chat and messages from accounts outside `MC_COMMAND_PLAYERS` are ignored.

## Updates during development

Commit and push source changes on the development machine so the desktop can receive them. On the desktop, use:

```powershell
git pull
docker compose up -d --build
```

Compose rebuilds changed code/dependencies and recreates the agent with the existing named volume. `docker.env` is ignored by Git and excluded from the image build, so local model settings and credentials stay on that desktop. Source is copied into the image; `docker compose restart` alone does not rebuild updated source.

## Persistent memory and saved experience

The `agent-data` named volume is mounted at `/app/data`. With the default project name `minecraft-agent`, Docker names it `minecraft-agent_agent-data`. It contains:

- `memory/`: SQLite checkpoints, world history, and optional Graphiti graph files.
- `learning/`: saved outcomes and procedural experience.
- `run/`: retained launcher, controller, and bot logs.

Process locks and readiness markers use `/tmp`, which is recreated for each container start. Rebuilding the image, restarting the container, and normal `docker compose down` preserve the data volume. Docker documents [recreating containers while preserving mounted volumes](https://docs.docker.com/reference/cli/docker/compose/up/) and [volume removal behavior](https://docs.docker.com/reference/cli/docker/compose/down/).

Use `docker compose stop` to pause the agent, and `docker compose start` to resume it. The supervisor shuts down its controller so SQLite and optional graph data can close cleanly. Keep the same Compose project name to reconnect to the same data volume. `docker compose down --volumes` and deleting the volume in Docker Desktop deliberately erase the saved data; those commands are not part of the update workflow.

To back up the data, stop the agent before copying its databases:

```powershell
docker compose stop
docker compose cp agent:/app/data ./agent-data-backup
docker compose start
```

## Another agent on the same desktop

Give it a separate private configuration, unique Minecraft username, and Compose project name:

```powershell
Copy-Item docker.env.example docker.agent3.env
notepad docker.agent3.env
$env:AGENT_ENV_FILE = "docker.agent3.env"
docker compose -p minecraft-agent-3 up -d --build
Remove-Item Env:AGENT_ENV_FILE
```

Configure the same model endpoint if both agents should share an external model service. Repeat the project-name and configuration-file selection when updating/stopping that agent. Each project gets its own memory volume and isolated controller port; no `--bridge-port` override is needed between containers. Avoid scaling replicas of the same service with the same Minecraft username.

## Validation limits

The Compose specification can be checked without a running Docker engine. Launcher readiness, child-process shutdown, addressed commands, and checkpoint persistence can also be tested directly. Building and running the Linux image, first-use volume permissions, and Windows/Desktop networking still require a running Docker engine. The current development session cannot start Docker Desktop because its filesystem permissions block Docker's application-data directory.
