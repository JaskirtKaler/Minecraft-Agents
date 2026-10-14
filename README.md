# Minecraft Agents

A Minecraft assistant with a Python controller, Mineflayer bot, and persistent world memory. Agents use an external model API and can share one Minecraft server on a trusted home network.

The controller can attempt gathering, crafting, delivery, and construction through observed, checked actions. `status`, `stop`, `inventory`, and `memory` work directly. Controlled tests and simulator results have distinct limits; see [architecture and capabilities](docs/architecture.md) and [recorded validation](docs/validation.md).

## Choose a launch setup

| Setup | What it runs | Guide |
| --- | --- | --- |
| Server host | Purpur, one bot, and its Python controller | [Host operations](docs/operations.md) |
| Windows desktop with Docker | One additional bot/controller, with a persistent data volume | [Docker agent setup](docs/docker-agent.md) |
| Additional agent from source | One bot/controller joining an existing server | [Agent launch](docs/operations.md#adding-an-agent-without-another-server); [Windows WSL/Conda](docs/windows-agent.md) |

Model services and weights are configured separately. No router port forwarding is needed for this home-network setup.

## Server host from source

Install a Java 21 JDK. Create the Python 3.12 / Node.js 22 environment and install the locked bot dependencies:

```bash
conda env create -f environment.yml
conda activate minecraft-agent
npm ci --prefix bot
cp .env.example .env
```

Preserve an existing `.env`; configure the model endpoint and model names in your private copy. For a fresh checkout, review Minecraft's EULA and follow [server bootstrap](docs/repository.md#fresh-checkout) to populate the libraries needed by the plugin builders.

```bash
./start.sh --check
./start.sh
```

Wait for `READY`, then join Minecraft Java at **127.0.0.1:25565**. Other home-network devices use the LAN address printed by the launcher. The server/bot use Minecraft 1.20.1, with ViaVersion for newer clients. Training mode is Peaceful with no hunger, while retaining Survival inventory and crafting. Ctrl+C saves the world and stops the launcher's services.

Address chat commands to one agent: `@AI_Agent mine 3 oak logs`, `@AI_Agent status`, or `@AI_Agent stop`. Human names in `MC_COMMAND_PLAYERS` may issue commands; plain chat and other senders are ignored.

## Additional desktop agent with Docker

Clone the repository on the Windows desktop, start Docker Desktop with Linux containers, and create its private configuration in PowerShell:

```powershell
Copy-Item docker.env.example docker.env
notepad docker.env
```

Set the Mac's LAN address, a unique name such as `AI_Agent_2`, the existing world ID, and the external model API values. The [Docker guide](docs/docker-agent.md) explains each field, backups, and running several agents.

```powershell
docker compose config --quiet
docker compose up -d --build
```

For updates, pull the committed source and run `docker compose up -d --build` again. The named volume keeps memory, learned experiences, and logs. Docker includes the Python/Node runtime; Conda is optional for this setup.

## Checks and practice

```bash
./check.sh
./practice.sh --list
./learn.sh --backend simulator --suite construction --check
./rl.sh --check
```

`check.sh` runs offline Python, Node, shell, and Java contracts. Gameplay regression, model-led practice, and optional policy training have separate commands and evidence. See [test commands](tests/README.md), [practice and evaluation](docs/practice.md), and the [RL guide](docs/rl.md).

## Code and documentation

The [repository map](docs/repository.md) describes source ownership, launch helpers, and private runtime paths. Root commands remain the entry points; generated worlds, dependencies, and agent data stay at their existing locations.

- [Architecture and capabilities](docs/architecture.md)
- [Host operations and LAN connections](docs/operations.md)
- [Docker agent setup](docs/docker-agent.md)
- [Windows source setup](docs/windows-agent.md)
- [Practice and evaluation](docs/practice.md)
- [Implementation roadmap](docs/roadmap.md)
- [Structured observation contract](docs/observations.md)
- [Hackathon demo requirements](docs/demo.md)

The intended Nebius × NVIDIA hackathon demo uses an NVIDIA open-source model through Nebius at runtime; local inference supports development. See the [demo requirements](docs/demo.md) for the recorded checklist and official rules.

## License

[MIT](LICENSE)
