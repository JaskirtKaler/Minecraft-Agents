"""Frozen-policy evaluations, local seed provenance and descriptive failure traces.

Full simulator maps are grading/debugging data only; they never enter the actor.
No demonstration, optimizer, weight-update, or checkpoint-save calls exist here.
"""
import asyncio
from collections import Counter, deque
import hashlib
import json
from math import hypot
from pathlib import Path

import numpy as np

from rl.navigation import DIRECTIONS, FEATURE_VERSION
from rl.ppo import ActorCritic


def policy_hash(model):
    digest = hashlib.sha256(f'{FEATURE_VERSION}:{model.version}'.encode())
    for name, array in sorted(model.parameters.items()):
        digest.update(f'{name}:{array.dtype}:{array.shape}'.encode())
        digest.update(array.tobytes(order='C'))
    return digest.hexdigest()


def known_seed_ranges(history_root, backend):
    """Exclude all recorded attempts, not only successful teacher episodes."""
    ranges, checked, unreadable = [], 0, []
    for path in sorted(Path(history_root).glob('*/report.json')):
        try:
            report = json.loads(path.read_text())
            if not isinstance(report, dict):
                raise ValueError('Report is not an object.')
            if report.get('backend') != backend:
                continue
            checked += 1
            for key in ('held_out_seeds', 'evaluation_seeds'):
                for seed in report.get(key, []):
                    if type(seed) is int:
                        ranges.append((seed, seed))
            settings = report.get('settings', {})
            if report.get('mode') != 'evaluation-only':
                seed = settings.get('seed', report.get('seed'))
                episodes, updates = settings.get('episodes', 0), settings.get('updates', 0)
                demos = settings.get('demonstrations', 0)
                if all(type(n) is int and n >= 0 for n in (seed, episodes, updates, demos)):
                    if episodes * updates:
                        ranges.append((seed, seed + episodes * updates - 1))
                    if demos:
                        ranges.append((seed + 50_000, seed + 50_000 + demos - 1))
        except (OSError, ValueError, TypeError, AttributeError):
            unreadable.append(str(path))
    return ranges, checked, unreadable


def prepare_evaluation(args, history_root):
    path = Path(args.checkpoint).resolve(strict=True)
    before_file = hashlib.sha256(path.read_bytes()).hexdigest()
    model = ActorCritic.load(path, seed=args.seed)
    if hashlib.sha256(path.read_bytes()).hexdigest() != before_file:
        raise RuntimeError('Checkpoint changed while loading; refusing a mixed evaluation.')
    seeds = list(range(args.evaluation_start_seed, args.evaluation_start_seed + args.eval_episodes))
    ranges, checked, unreadable = known_seed_ranges(history_root, args.backend)
    overlaps = [seed for seed in seeds if any(low <= seed <= high for low, high in ranges)]
    if overlaps:
        raise ValueError(f'Evaluation seeds overlap saved local training/evaluation runs: {overlaps[:8]}. Choose a new --evaluation-start-seed.')
    if unreadable:
        raise ValueError('Cannot audit unreadable local run reports: ' + ', '.join(unreadable[:3]))
    return {'model': model, 'checkpoint': path, 'file_hash': before_file,
            'policy_hash': policy_hash(model), 'version': model.version,
            'adam_steps': model.adam_steps, 'seeds': seeds,
            'provenance': {'reports_checked': checked, 'known_seed_ranges': len(ranges),
                           'checkpoint_report_present': (path.parent / 'report.json').is_file(),
                           'disjoint_from_known_local_runs': True,
                           'scope': 'Saved local reports only; external or deleted experiment history is not auditable.'}}


def simulator_diagnostics(env, case):
    """BFS oracle and layout fingerprint. Never passed to encode/model.act."""
    if env.backend != 'grid-simulator':
        return None, None
    layout = {'generator_version': 1, 'backend': env.backend, 'bounds': [-4, 4, -4, 4],
              'walls': [list(p) for p in sorted(env.walls)],
              'start_cell': case['start_cell'], 'goal': case['goal']}
    fingerprint = hashlib.sha256(json.dumps(layout, sort_keys=True).encode()).hexdigest()
    start, goal = tuple(case['start_cell'][::2]), tuple(case['goal'][::2])
    queue, seen = deque([(start, 0)]), {start}
    while queue:
        (x, z), distance = queue.popleft()
        if (x, z) == goal:
            return {'layout_hash': fingerprint, 'shortest_path_steps': distance}, layout
        for dx, dz in DIRECTIONS[:4]:
            p = x + dx, z + dz
            if p not in seen and env._open(*p):
                seen.add(p)
                queue.append((p, distance + 1))
    return {'layout_hash': fingerprint, 'shortest_path_steps': None}, layout


def failure_category(case, decisions):
    """Describe observed behavior, not an unproven root cause."""
    if case['success']:
        return 'success'
    reason = case['termination_reason']
    if reason != 'step_limit':
        return reason
    positions = [d['next_position'] for d in decisions]
    for period in range(2, min(8, len(positions) // 2) + 1):
        if (positions[-period:] == positions[-2 * period:-period] and
                len({tuple(p) for p in positions[-period:]}) > 1):
            return 'cycle_at_step_limit'
    if case['moving_revisit_fraction'] >= 0.35:
        return 'revisits_at_step_limit'
    return 'step_limit'


def summary(cases):
    successes = [c for c in cases if c['success']]
    return {'episodes': len(cases), 'successes': len(successes),
            'success_rate': len(successes) / len(cases),
            'mean_steps': float(np.mean([c['steps'] for c in cases])),
            'mean_success_steps': float(np.mean([c['steps'] for c in successes])) if successes else None,
            'mean_moving_revisit_fraction': float(np.mean([c['moving_revisit_fraction'] for c in cases])),
            'failure_categories': dict(Counter(c['failure_category'] for c in cases if not c['success'])),
            'cases': cases}


async def evaluation_experiment(env, args, directory, report, prepared, episode_runner):
    directory = Path(directory)
    model, seeds = prepared['model'], prepared['seeds']
    report.update(evaluation_seeds=seeds, checkpoint=str(prepared['checkpoint']),
                  policy_version=model.version, feature_version=FEATURE_VERSION,
                  weight_updates=0, training_episodes=0, demonstration_episodes=0,
                  decision_mode='deterministic masked argmax', seed_provenance=prepared['provenance'],
                  checkpoint_sha256_before=prepared['file_hash'], policy_sha256_before=prepared['policy_hash'],
                  evaluations=[], promoted_to_default_agent=False)
    caps = [args.max_steps] + ([args.compare_max_steps] if args.compare_max_steps is not None else [])
    # This stream contains failures only, one complete compact trajectory per
    # line. Features and probabilities reconstruct what the policy decided on.
    with (directory / 'failures.jsonl').open('x') as failures:
        expected_layouts = {}
        for cap in caps:
            policy_cases, baseline_cases = [], []
            print(f'Evaluation only: {len(seeds)} seeds, {cap}-step cap, frozen policy v{model.version}.', flush=True)
            for index, seed in enumerate(seeds):
                decisions = []
                _, case = await episode_runner(env, model, seed, cap, decision_trace=decisions)
                case['failure_category'] = failure_category(case, decisions)
                case['final_goal_distance'] = hypot(case['goal'][0] - case['final_cell'][0],
                                                    case['goal'][2] - case['final_cell'][2])
                oracle, layout = simulator_diagnostics(env, case)
                if oracle:
                    case.update(oracle)
                    fingerprint = oracle['layout_hash']
                    if seed in expected_layouts and expected_layouts[seed] != fingerprint:
                        raise RuntimeError('Same seed produced a different paired layout; discard comparison.')
                    expected_layouts[seed] = fingerprint
                    case['shortest_path_fits_budget'] = oracle['shortest_path_steps'] is not None and oracle['shortest_path_steps'] <= cap
                policy_cases.append(case)
                if not case['success']:
                    failures.write(json.dumps({'schema_version': 1, 'backend': env.backend, 'max_steps': cap,
                        'training': False, 'policy_version': model.version, 'feature_version': FEATURE_VERSION,
                        'checkpoint_sha256': prepared['file_hash'],
                        'case': case, 'layout_oracle': layout, 'decisions': decisions,
                        'note': 'Layout oracle is for debugging/grading only, not policy input.'}, allow_nan=False) + '\n')
                    failures.flush()
                _, baseline = await episode_runner(env, model, seed, cap, baseline=True)
                if case['start_cell'] != baseline['start_cell'] or case['goal'] != baseline['goal']:
                    raise RuntimeError('Policy/baseline fixtures differ; discard comparison.')
                baseline_oracle, _ = simulator_diagnostics(env, baseline)
                if baseline_oracle and baseline_oracle['layout_hash'] != case['layout_hash']:
                    raise RuntimeError('Policy/baseline maps differ; discard comparison.')
                baseline['failure_category'] = 'success' if baseline['success'] else baseline['termination_reason']
                baseline_cases.append(baseline)
                if (index + 1) % 10 == 0 or index + 1 == len(seeds):
                    print(f'  {index + 1}/{len(seeds)} layouts: policy {sum(c["success"] for c in policy_cases)}, '
                          f'heuristic {sum(c["success"] for c in baseline_cases)} goals reached.', flush=True)
                # Allow wall-clock deadline/cancellation even in the CPU simulator.
                await asyncio.sleep(0)
            report['evaluations'].append({'max_steps': cap, 'policy': summary(policy_cases),
                                          'heuristic': summary(baseline_cases)})
            (directory / 'report.json').write_text(json.dumps(report, indent=2, allow_nan=False))
        report['unique_layouts'] = len(set(expected_layouts.values())) if expected_layouts else None
    report['checkpoint_sha256_after'] = hashlib.sha256(prepared['checkpoint'].read_bytes()).hexdigest()
    report['policy_sha256_after'] = policy_hash(model)
    report['weights_unchanged'] = (report['checkpoint_sha256_before'] == report['checkpoint_sha256_after'] and
                                  report['policy_sha256_before'] == report['policy_sha256_after'] and
                                  model.version == prepared['version'] and model.adam_steps == prepared['adam_steps'])
    if not report['weights_unchanged']:
        raise RuntimeError('Evaluation mutated a checkpoint, policy weights, or optimizer; results not accepted.')
    if len(caps) == 2:
        initial = {c['seed']: c for c in report['evaluations'][0]['policy']['cases']}
        longer = report['evaluations'][1]['policy']['cases']
        report['step_budget_comparison'] = {
            'additional_goals_reached': sum(not initial[c['seed']]['success'] and c['success'] for c in longer),
            'previous_successes_lost': sum(initial[c['seed']]['success'] and not c['success'] for c in longer),
            'failures_even_with_larger_budget': sum(not c['success'] for c in longer)}
    report['completed'] = True
    (directory / 'report.json').write_text(json.dumps(report, indent=2, allow_nan=False))
    write_summary(directory, report)
    for result in report['evaluations']:
        p, h = result['policy'], result['heuristic']
        print(f"RESULT {result['max_steps']} steps: policy {p['successes']}/{p['episodes']}; "
              f"heuristic {h['successes']}/{h['episodes']}. No weight updates.", flush=True)


def write_summary(directory, report):
    lines = ['# Frozen navigation policy evaluation', '',
             f"Backend: {report['backend']}. Policy version: {report['policy_version']}. "
             'No training, demonstration collection, LLM calls, or automatic deployment.', '',
             ('Simulator results measure the synthetic flat-grid task, not Minecraft performance.'
              if report['backend'] == 'simulator' else
              'Minecraft results measure same-level walking in disposable arenas, not general gameplay.'), '',
             '| Step cap | Frozen policy | Local heuristic |', '| --- | --- | --- |']
    for r in report['evaluations']:
        p, h = r['policy'], r['heuristic']
        lines.append(f"| {r['max_steps']} | {p['successes']}/{p['episodes']} | {h['successes']}/{h['episodes']} |")
    seeds = report['evaluation_seeds']
    lines.extend(['', f"Seeds: {seeds[0]}–{seeds[-1]}; "
                  f"{report['seed_provenance']['reports_checked']} saved local reports checked. "
                  'Seeds were checked against saved local training/evaluation reports. '
                  'Deleted or external histories cannot be audited. Both controllers use the same paired seeds, '
                  'local observation encoder and destination.', '',
                  'Checkpoint bytes, policy parameters, policy version, and optimizer step count remained unchanged.', '',
                  '## Observed failure behavior', ''])
    for r in report['evaluations']:
        counts = r['policy']['failure_categories']
        lines.append(f"At the {r['max_steps']}-step cap: " + (', '.join(f'{k}: {v}' for k, v in counts.items()) or 'no failures') + '.')
    if 'step_budget_comparison' in report:
        comparison = report['step_budget_comparison']
        lines.extend(['', f"The larger budget reached {comparison['additional_goals_reached']} additional goals; "
                      f"{comparison['failures_even_with_larger_budget']} layouts still failed. "
                      f"Previously successful layouts lost: {comparison['previous_successes_lost']}."])
    if report['backend'] == 'simulator':
        cases = report['evaluations'][0]['policy']['cases']
        distances = [c['shortest_path_steps'] for c in cases]
        if all(distance is not None for distance in distances):
            lines.extend(['', f"The grading oracle's longest shortest path was {max(distances)} steps. "
                          f"{sum(c['shortest_path_fits_budget'] for c in cases)}/{len(cases)} layouts "
                          'had a shortest route within the smaller step budget. The actor did not receive those routes.'])
    lines.extend(['', 'Cycle/revisit labels describe the trajectory; they are not a diagnosis of the model or reward design. '
                  'Longer-budget gains show that additional steps helped those layouts, not that step count is the only problem.', '',
                  '`report.json` contains per-layout comparisons and simulator shortest-path grading. '
                  '`failures.jsonl` contains failed policy trajectories, exact feature vectors, masks, action probabilities, '
                  'and simulator maps for debugging. The full map and shortest path never enter the policy.', ''])
    (Path(directory) / 'summary.md').write_text('\n'.join(lines))
