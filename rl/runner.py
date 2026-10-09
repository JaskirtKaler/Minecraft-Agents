"""Bounded local navigation training or frozen evaluation. Normal worlds and LLMs are untouched."""
import argparse
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import signal
import time
import uuid

import numpy as np

from rl.environments import GridNavigationEnv, MinecraftNavigationEnv
from rl.navigation import ACTIONS, DIRECTIONS, cell_position, encode, transition_reward
from rl.ppo import ActorCritic

ROOT = Path(__file__).resolve().parents[1]


@asynccontextmanager
async def minecraft_backend(directory, seed):
    from agent.bridge import MineflayerBridge
    from practice.runner import BOT_NAME, ServerConsole, free_port, java_binary, prepare_server
    java = java_binary()
    builder = await asyncio.create_subprocess_exec('bash', str(ROOT / 'server-plugins/practice-oracle/build.sh'),
                                                  env={**os.environ, 'MINECRAFT_JAVA': java})
    try:
        if await builder.wait() != 0:
            raise RuntimeError('Practice oracle did not compile.')
    finally:
        if builder.returncode is None:
            builder.terminate()
            await builder.wait()
    nonce = str(uuid.uuid4())
    port = free_port()
    prepare_server(directory / 'server', nonce, port)
    bridge = MineflayerBridge(host='127.0.0.1', port=0)
    logging.getLogger('BridgeServer').setLevel(logging.WARNING)
    ws = await bridge.start()
    console, bot, server = None, None, None
    with (directory / 'server.log').open('w') as server_log, (directory / 'bot.log').open('w') as bot_log:
        try:
            server = await asyncio.create_subprocess_exec(java, '-Xms512M', '-Xmx1G',
                f'-Dminecraftagents.practice={nonce}', f'-Dminecraftagents.practice.seed={seed}',
                '-jar', 'server.jar', '--nogui', cwd=directory / 'server',
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            console = ServerConsole(server, server_log)
            await asyncio.wait_for(console.ready.wait(), 120)
            await asyncio.wait_for(console.oracle_ready.wait(), 10)
            env = {**os.environ, 'MC_HOST': '127.0.0.1', 'MC_PORT': str(port), 'MC_USERNAME': BOT_NAME,
                   'MC_VERSION': '1.20.1', 'MC_WORLD_ID': f'practice:rl:{nonce}',
                   'WS_URL': f'ws://127.0.0.1:{ws.sockets[0].getsockname()[1]}',
                   'ENABLE_VIEWER': 'false', 'ALLOW_EXPERIMENTAL_CODE': 'false', 'ENABLE_RL_NAVIGATION': 'true'}
            bot = await asyncio.create_subprocess_exec('node', str(ROOT / 'bot/index.js'), cwd=ROOT, env=env,
                                                       stdout=bot_log, stderr=asyncio.subprocess.STDOUT)
            for _ in range(100):
                if bot.returncode is not None:
                    raise RuntimeError('Disposable RL bot exited; see bot.log.')
                if bridge.active_client and (await bridge.get_state()).get('ready'):
                    break
                await asyncio.sleep(0.2)
            else:
                raise RuntimeError('Disposable RL bot did not spawn.')
            print(f'Disposable Minecraft RL server ready on 127.0.0.1:{port}; NOT your normal world.', flush=True)
            yield MinecraftNavigationEnv(console, bridge)
        finally:
            if bot and bot.returncode is None:
                bot.terminate()
                try:
                    await asyncio.wait_for(bot.wait(), 10)
                except asyncio.TimeoutError:
                    bot.kill()
                    await bot.wait()
            if console:
                await console.stop()
            elif server and server.returncode is None:
                server.terminate()
                await server.wait()
            ws.close()
            await ws.wait_closed()


def greedy_action(frame, goal, visits):
    """Local heuristic baseline; it gets exactly the policy's local view and goal."""
    x, y, z = cell_position(frame)
    choices = []
    for action, (dx, dz) in enumerate(DIRECTIONS):
        if frame['actionMask'][action]:
            distance = abs(goal[0] - x - dx) + abs(goal[2] - z - dz)
            choices.append((distance + 1.5 * visits.get((x + dx, y, z + dz), 0) + (0.1 if action == 4 else 0), action))
    return min(choices)[1]


async def episode(env, model, seed, max_steps, *, baseline=False, training=False, trace=None, decision_trace=None):
    if baseline and training:
        raise ValueError('Heuristic actions cannot be collected as on-policy PPO experience.')
    frame, evidence, goal = await env.reset(seed)
    visits = {cell_position(frame): 1}
    total_reward, stale = 0.0, 0
    positions = [cell_position(frame)]
    waits, moving_revisits = 0, 0
    transitions = []
    started = time.monotonic()
    for step in range(max_steps):
        features, mask = encode(frame, goal, visits)
        action, logp, value = model.act(features, mask, deterministic=not training)
        if baseline:
            action = greedy_action(frame, goal, visits)
        probabilities = None
        if baseline or decision_trace is not None:
            probabilities = model.forward(np.array([features]), np.array([mask]))[0][0]
            logp = float(np.log(max(probabilities[action], 1e-300)))
        next_frame, after, elapsed, outcome = await env.step(action)
        failed = not (outcome.get('success') is True and outcome.get('verified') is True)
        reward, discount, terminated, success = transition_reward(evidence, after, goal, elapsed, failed)
        position = cell_position(next_frame)
        stale = stale + 1 if position == cell_position(frame) else 0
        waits += int(action == 4)
        moving_revisits += int(position != cell_position(frame) and visits.get(position, 0) > 0)
        positions.append(position)
        visits[position] = visits.get(position, 0) + 1
        truncated = not terminated and (step + 1 == max_steps or stale >= 6 or failed)
        next_features, next_mask = encode(next_frame, goal, visits)
        # Time-limit truncation bootstraps the final observation. A true
        # terminal outcome does not; GAE never carries into the next reset.
        next_value = 0 if terminated else model.value(next_features, next_mask)
        transition = {'features': features.tolist(), 'mask': mask.tolist(), 'action': action,
            'logp': logp, 'value': value, 'reward': reward, 'discount': discount, 'next_value': next_value,
            'elapsed_seconds': elapsed, 'terminated': terminated, 'truncated': truncated,
            'policy_version': model.version}
        if training:
            transitions.append(transition)
        if decision_trace is not None:
            decision_trace.append({'step': step + 1, 'position': list(cell_position(frame)),
                'next_position': list(position), 'walkability': frame['walkability'],
                'features': features.tolist(), 'mask': mask.tolist(), 'action': action,
                'action_name': ACTIONS[action], 'model_probabilities': probabilities.tolist(),
                'model_value': value, 'reward': reward, 'outcome': outcome})
        if trace:
            record = {'seed': seed, 'step': step + 1, 'goal': list(goal), 'backend': env.backend,
                      'training': training, 'baseline': baseline, 'observation': frame,
                      'next_observation': next_frame, 'before_evidence': evidence, 'after_evidence': after,
                      'outcome': outcome, **transition}
            trace.write(json.dumps(record, allow_nan=False) + '\n')
            trace.flush()
        total_reward += reward
        frame, evidence = next_frame, after
        if terminated or truncated:
            reason = ('success' if success else 'terminal_failure' if terminated else
                      'tool_failure' if failed else 'stationary' if stale >= 6 else 'step_limit')
            return transitions, {'seed': seed, 'success': success, 'steps': step + 1,
                'reward': total_reward, 'terminated': terminated, 'truncated': truncated,
                'stalled': stale >= 6, 'duration_seconds': time.monotonic() - started,
                'termination_reason': reason, 'start_cell': list(positions[0]), 'goal': list(goal),
                'final_cell': list(position), 'unique_cells': len(set(positions)),
                'wait_actions': waits, 'moving_revisits': moving_revisits,
                'moving_revisit_fraction': moving_revisits / max(1, step + 1 - waits)}
    raise AssertionError('Episode failed to reach its bounded end.')


async def evaluate(env, model, seeds, max_steps, baseline=False):
    cases = [(await episode(env, model, seed, max_steps, baseline=baseline))[1] for seed in seeds]
    return {'episodes': len(cases), 'successes': sum(c['success'] for c in cases),
            'success_rate': sum(c['success'] for c in cases) / len(cases),
            'mean_steps': float(np.mean([c['steps'] for c in cases])), 'cases': cases}


async def demonstrations(env, args, stream):
    """Local heuristic teacher. Only independently successful episodes are labels."""
    accepted, successes = [], 0
    for index in range(args.demonstrations):
        seed = args.seed + 50_000 + index  # disjoint from PPO and evaluation seeds
        frame, evidence, goal = await env.reset(seed)
        visits = {cell_position(frame): 1}
        samples = []
        for _ in range(args.max_steps):
            features, mask = encode(frame, goal, visits)
            action = greedy_action(frame, goal, visits)
            next_frame, after, elapsed, outcome = await env.step(action)
            failed = not (outcome.get('success') and outcome.get('verified'))
            _, _, terminated, success = transition_reward(evidence, after, goal, elapsed, failed)
            samples.append({'features': features.tolist(), 'mask': mask.tolist(), 'action': action,
                            'seed': seed, 'backend': env.backend, 'goal': list(goal)})
            position = cell_position(next_frame)
            visits[position] = visits.get(position, 0) + 1
            frame, evidence = next_frame, after
            if terminated or failed:
                break
        if success:
            successes += 1
            accepted.extend(samples)
            for sample in samples:
                stream.write(json.dumps(sample) + '\n')
            stream.flush()
        await asyncio.sleep(0)
    return accepted, successes


async def experiment(env, args, directory, report):
    model = ActorCritic.load(args.checkpoint, seed=args.seed) if args.checkpoint else ActorCritic(args.seed)
    report['initial_policy_version'] = model.version
    model.save(directory / 'initial_policy.npz')
    seeds = [args.seed + 100_000 + i for i in range(args.eval_episodes)]
    report['held_out_seeds'] = seeds
    print('Evaluating the untrained/loaded policy and local heuristic on held-out seeds.', flush=True)
    report['initial_evaluation'] = await evaluate(env, model, seeds, args.max_steps)
    report['heuristic_evaluation'] = await evaluate(env, model, seeds, args.max_steps, baseline=True)
    if env.backend == 'mineflayer':
        report['pathfinder_control'] = [await env.pathfinder_control(seed) for seed in seeds]
    if args.demonstrations:
        with (directory / 'demonstrations.jsonl').open('x') as stream:
            samples, successes = await demonstrations(env, args, stream)
        report['demonstrations'] = {'requested': args.demonstrations, 'successful_episodes': successes,
                                    'accepted_samples': len(samples), 'teacher': 'local heuristic'}
        if not samples:
            raise RuntimeError('No independently successful demonstrations; refusing to clone failed trajectories.')
        report['cloning'] = model.clone(samples)
        model.save(directory / 'warmstart_policy.npz')
        print(f"Warm-start: {successes}/{args.demonstrations} successful demonstrations, {len(samples)} labels; "
              f"supervised loss {report['cloning']['loss_before']:.3f} → {report['cloning']['loss_after']:.3f}.", flush=True)
        report['warmstart_evaluation'] = await evaluate(env, model, seeds, args.max_steps)
        await asyncio.sleep(0)
    report['updates'] = []
    with (directory / 'transitions.jsonl').open('x') as trace:
        for update in range(args.updates):
            rollout, episodes = [], []
            for index in range(args.episodes):
                seed = args.seed + update * args.episodes + index
                transitions, stats = await episode(env, model, seed, args.max_steps, training=True, trace=trace)
                rollout.extend(transitions)
                episodes.append(stats)
            metrics = model.update(rollout)
            report['updates'].append({'metrics': metrics, 'episodes': episodes})
            print(f"PPO update {update + 1}/{args.updates}: {len(rollout)} fresh transitions, "
                  f"{metrics['gradient_steps']} gradient steps, {sum(e['success'] for e in episodes)}/{len(episodes)} goals reached.", flush=True)
            (directory / 'report.json').write_text(json.dumps(report, indent=2, allow_nan=False))
            # Simulator calls are deliberately cheap synchronous computations;
            # yield so cancellation/time-budget callbacks still get scheduled.
            await asyncio.sleep(0)
    model.save(directory / 'policy.npz')
    report['checkpoint'] = str(directory / 'policy.npz')
    report['final_policy_version'] = model.version
    print('Evaluating the updated policy on the same held-out seeds; no weight updates here.', flush=True)
    report['final_evaluation'] = await evaluate(env, model, seeds, args.max_steps)
    report['completed'] = True
    report['promoted_to_default_agent'] = False
    print(f"Held-out goals: {report['initial_evaluation']['successes']} before → "
          f"{report['final_evaluation']['successes']} after / {args.eval_episodes}; "
          f"heuristic {report['heuristic_evaluation']['successes']}. Experimental, not auto-deployed.", flush=True)


async def run(args):
    loop = asyncio.get_running_loop()
    owner = asyncio.current_task()
    loop.add_signal_handler(signal.SIGTERM, owner.cancel)
    directory = ROOT / 'data/rl' / (datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S') + '-' + str(uuid.uuid4())[:8])
    directory.mkdir(parents=True)
    report = {'schema_version': 1, 'backend': args.backend, 'inference_calls': 0, 'completed': False,
              'mode': 'evaluation-only' if args.evaluate_only else 'training',
              'seed': args.seed, 'settings': vars(args), 'scope': 'same-level-adjacent-navigation',
              'evaluation_kind': 'Minecraft server evidence' if args.backend == 'minecraft' else 'synthetic grid only; not Minecraft performance',
              'promoted_to_default_agent': False}
    print(f'RL artifacts: {directory}', flush=True)
    work = 'frozen-policy evaluation; zero weight updates' if args.evaluate_only else 'CPU weight training'
    print(f'Backend: {args.backend}; {work}; zero LLM/API calls; {args.time_budget}s time budget.', flush=True)
    try:
        async with asyncio.timeout(args.time_budget):
            if args.evaluate_only:
                from rl.evaluation import prepare_evaluation, evaluation_experiment
                # Validate checkpoint and seed provenance before starting any
                # optional Minecraft processes or world preparation.
                prepared = prepare_evaluation(args, ROOT / 'data/rl')
            async def execute(env):
                if args.evaluate_only:
                    await evaluation_experiment(env, args, directory, report, prepared, episode)
                else:
                    await experiment(env, args, directory, report)
            if args.backend == 'minecraft':
                async with minecraft_backend(directory, args.seed) as env:
                    await execute(env)
            else:
                await execute(GridNavigationEnv())
    except BaseException as error:
        report['error'] = f'{type(error).__name__}: {error}'
        raise
    finally:
        (directory / 'report.json').write_text(json.dumps(report, indent=2, allow_nan=False))
        loop.remove_signal_handler(signal.SIGTERM)
    label = 'evaluation report (checkpoint unchanged)' if args.evaluate_only else 'checkpoint and report'
    print(f"Saved {label}: {directory / 'report.json'}", flush=True)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', choices=('simulator', 'minecraft'), default='simulator')
    parser.add_argument('--updates', type=int, help='PPO updates; training default 8, not used in evaluation-only mode')
    parser.add_argument('--episodes', type=int, help='Fresh training episodes per PPO update; default 4')
    parser.add_argument('--max-steps', type=int, default=24)
    parser.add_argument('--eval-episodes', type=int, default=4)
    parser.add_argument('--seed', type=int, default=17)
    parser.add_argument('--time-budget', type=int, default=180, help='Work deadline including Minecraft startup; owned services get bounded cleanup afterward')
    parser.add_argument('--checkpoint', help='Compatible checkpoint; required and frozen for evaluation-only, weight initialization for training')
    parser.add_argument('--demonstrations', type=int, default=0,
                        help='Optional 0–128 teacher episodes for supervised warm-start before fresh PPO rollouts')
    parser.add_argument('--evaluate-only', action='store_true', help='Frozen checkpoint, new seeds, paired baseline, failure traces; no training')
    parser.add_argument('--evaluation-start-seed', type=int, default=2_000_000, help='Evaluation-only seed range start; rejects overlap with saved local runs')
    parser.add_argument('--compare-max-steps', type=int, help='Optional larger evaluation-only step cap on the same layouts (up to 64)')
    parser.add_argument('--check', action='store_true', help='Print preflight only; no services or weight updates')
    args = parser.parse_args(argv)
    if args.evaluate_only:
        if not args.checkpoint or args.demonstrations or args.updates is not None or args.episodes is not None:
            parser.error('--evaluate-only requires --checkpoint and cannot use demonstrations, updates, or training episodes.')
        args.updates, args.episodes = 0, 0
    else:
        if args.compare_max_steps is not None:
            parser.error('--compare-max-steps requires --evaluate-only.')
        args.updates = 8 if args.updates is None else args.updates
        args.episodes = 4 if args.episodes is None else args.episodes
    if not args.evaluate_only and not (1 <= args.updates <= 50 and 1 <= args.episodes <= 16):
        parser.error('Training settings: updates 1–50, episodes 1–16.')
    max_evaluations = 256 if args.evaluate_only and args.backend == 'simulator' else 16 if args.evaluate_only else 32
    if not (4 <= args.max_steps <= 64 and 1 <= args.eval_episodes <= max_evaluations and 0 <= args.demonstrations <= 128 and
            0 <= args.seed <= 1_000_000_000 and 10 <= args.time_budget <= 600):
        parser.error(f'Bounded settings: steps 4–64, eval 1–{max_evaluations}, seed 0–1e9, demonstrations 0–128, budget 10–600s.')
    if (not 0 <= args.evaluation_start_seed <= 1_000_000_000 or
            args.evaluate_only and args.evaluation_start_seed + args.eval_episodes - 1 > 1_000_000_000 or
            args.compare_max_steps is not None and not args.max_steps < args.compare_max_steps <= 64):
        parser.error('Evaluation seeds must be 0–1e9; comparison step cap must be larger than max-steps and at most 64.')
    if args.updates * args.episodes * args.max_steps > 20_000:
        parser.error('One invocation may collect at most 20,000 training transitions.')
    return args


def main(argv=None):
    args = parse_args(argv)
    if args.check:
        model = ActorCritic.load(args.checkpoint) if args.checkpoint else ActorCritic(args.seed)
        print(f'NumPy {np.__version__}; actor–critic {sum(p.size for p in model.parameters.values())} parameters; actions {ACTIONS}.')
        print('No model service needed. Simulator does not launch Minecraft. Minecraft uses its own guarded world/server.')
        print('No paid APIs; no normal-world writes; no automatic policy deployment.')
        return 0
    try:
        asyncio.run(run(args))
        return 0
    except KeyboardInterrupt:
        print('RL experiment interrupted; owned services stopped, logs retained.', flush=True)
        return 130
    except asyncio.CancelledError:
        print('RL experiment cancelled; owned services stopped, logs retained.', flush=True)
        return 143
    except Exception as error:
        print(f'RL experiment stopped: {type(error).__name__}: {error}. Check data/rl logs.', flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
