# Minecraft server host

This directory contains the bundled Purpur 1.20.1 server, compatibility plugins,
and intentional server configuration. The Python controller runs separately
and communicates with Mineflayer over a local WebSocket; no Python execution
plugin is required in Minecraft.

Use `./start.sh` from the repository root for the normal server + agent launch.
This directory's `start.sh` runs only Minecraft and is useful for the first
startup that populates cached libraries. Review Minecraft's EULA and set
`eula=true` in `eula.txt` before launching. The script never changes that file
or downloads a replacement server.

The bundled bootstrap jars remain in source control. Generated libraries,
worlds, logs, player caches, version history, and plugin telemetry are local
runtime data. Keep them at their existing paths. Operator, whitelist, ban,
and permission files are intentional server administration settings.

Offline logins support local bot identities; the configured server listens on
the trusted home network. See [operations](../docs/operations.md) for LAN
connections, player commands, and adding agents, and [repository organization](../docs/repository.md)
for the source/runtime map.
