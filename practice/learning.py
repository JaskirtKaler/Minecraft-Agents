"""Model-selected curriculum in varied disposable worlds, with independent grading."""
import json
from urllib.parse import urlparse

from agent.nebius_client import NebiusLLMClient
from agent.tool_agent import ModelToolAgent, validate_goals
from main import execute_chat_objective


class LoggedClient:
    def __init__(self, stream):
        self.client = NebiusLLMClient()
        self.stream = stream
        self.calls = 0

    async def generate_response(self, messages, **kwargs):
        self.calls += 1
        try:
            response = await self.client.generate_response(messages, **kwargs)
        except BaseException as error:
            self.stream.write(json.dumps({'call': self.calls, 'messages': messages, 'error': type(error).__name__}) + '\n')
            self.stream.flush()
            raise
        self.stream.write(json.dumps({'call': self.calls, 'messages': messages, 'response': response,
                                     'metrics': self.client.last_metrics}) + '\n')
        self.stream.flush()
        return response


def check_backend(settings, allow_hosted=False):
    host = urlparse(settings.nebius_base_url).hostname
    if host not in {'localhost', '127.0.0.1', '::1'} and not allow_hosted:
        raise ValueError('Learning defaults to local inference. Use --allow-hosted explicitly to permit this configured endpoint and its costs.')


def grade(goals, before, after):
    """Grade MODEL-DECLARED criteria against actual Bukkit state, not its claim."""
    evidence = []
    for goal in validate_goals(goals):
        kind = goal['kind']
        if kind.startswith('inventory_'):
            initial = before.get('inventory', {}).get(goal['item'], 0)
            actual = after.get('inventory', {}).get(goal['item'], 0)
            met = actual >= goal['count'] if kind == 'inventory_at_least' else actual - initial == goal['count']
        elif kind == 'container_gain':
            p = goal['position']
            key = ','.join(str(p[axis]) for axis in ('x', 'y', 'z'))
            initial = before.get('chest', {}).get(goal['item'], 0) if p == {'x': 3, 'y': 64, 'z': 3} else 0
            actual = after.get('containers', {}).get(key, {}).get(goal['item'], 0)
            met = actual - initial == goal['count']
        elif kind == 'block_is':
            key = ','.join(str(goal['position'][axis]) for axis in ('x', 'y', 'z'))
            initial = None
            actual = after.get('blocks', {}).get(key)
            met = actual == goal['block']
        elif kind == 'item_dropped':
            # This arena has no recipient inventory oracle. Never promote an
            # unsupported player handoff to a server-confirmed success.
            initial, actual, met = 0, None, False
        else:
            initial = before.get('position')
            actual = after.get('position', {})
            met = sum((actual.get(axis, float('inf')) - goal['position'][axis]) ** 2 for axis in ('x', 'y', 'z')) <= goal.get('radius', 1) ** 2
        evidence.append({'goal': goal, 'before': initial, 'observed': actual, 'met': met})
    safe = after.get('health', 0) >= before.get('health', 0) and not after.get('unsafe_breaks', 0)
    return all(e['met'] for e in evidence) and safe, evidence


async def learning_loop(args, console, bridge, controller, settings, directory, report, lock):
    import asyncio
    check_backend(settings, args.allow_hosted)
    with (directory / 'model.jsonl').open('w') as stream:
        client = LoggedClient(stream)
        agent = ModelToolAgent(bridge, client, controller.memory, settings)
        controller.tool_agent = agent
        report.update(mode='model-curriculum', model=settings.nebius_model, base_url=settings.nebius_base_url,
                      experience_directory=str(agent.library.directory), episodes_requested=args.episodes,
                      note='Experience-based learning, not weight training. Goals are model-declared; server grading does not prove perfect intent interpretation.')
        try:
            for index in range(args.episodes):
                await console.request('setup', 'explore')
                await asyncio.sleep(0.5)
                before = await console.request('snapshot')
                state = await bridge.get_state()
                choice = {'objective': args.objective, 'reason': 'User-selected learning objective'} if args.objective else await agent.choose_objective(state)
                objective = choice.get('objective')
                if not isinstance(objective, str) or not objective.strip() or len(objective) > 600:
                    raise ValueError('Curriculum model did not return a bounded objective')
                controller.dialogue['last'].clear()
                print(f"LEARN {index + 1}/{args.episodes}: {objective} ({choice.get('reason', '')})", flush=True)
                await execute_chat_objective(controller, bridge, objective, 'PracticeDriver', lock)
                result = controller.dialogue['last'].get('PracticeDriver', {})
                goals = result.get('data', {}).get('goals', [])
                after = await console.request('check', goals=goals)
                try:
                    passed, evidence = grade(goals, before, after)
                except ValueError:
                    passed, evidence = False, []
                # Do not promote a model-only success when the server disagrees.
                passed = passed and result.get('verified') is True and result.get('success') is True
                result.setdefault('data', {})['server_goal_evidence'] = evidence
                if result.get('verified') and not passed:
                    result.update(success=False, verified=False, status='unknown', message='Server oracle disagreed with client-side completion; not a mastered skill.')
                    lesson = 'Server verification failed. ' + result['data'].get('lesson', '')
                    key = result['data'].get('experience_id')
                    if key:
                        agent.library.update(key, lesson, result)
                    controller.memory.record_task(objective, {'name': 'server_audit'}, result, await bridge.get_state())
                await agent.flush_reflections()
                report['cases'].append({'name': f'episode_{index + 1}', 'objective': objective, 'curriculum': choice,
                                        'before': before, 'after': after, 'result': result, 'server_goal_evidence': evidence,
                                        'passed': passed, 'reward': 1 if passed else 0})
                report['inference_calls'] = client.calls
                (directory / 'report.json').write_text(json.dumps(report, indent=2))
                print(('CONFIRMED ' if passed else 'UNFINISHED ') + objective, flush=True)
                if result.get('data', {}).get('interrupted'):
                    raise RuntimeError('Interrupted episode; aborting rather than resetting over unsettled actions')
        finally:
            report['inference_calls'] = client.calls
            controller.tool_agent = None
            await agent.aclose()
            await client.client.client.close()
