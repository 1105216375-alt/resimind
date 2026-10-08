"""Audit completed repair development records without API calls or solving.

Unpack the published replay and live archives first; provide the pinned official
ChinaTravel checkout with its database. Gold is read only for post-run scoring.
Run this script from the source revision recorded in both manifests, or opt in
to checking the archived frozen sources with --use-archived-sources. That option
verifies ZIP entry hashes; it never executes or extracts code from the archive.
"""
from pathlib import Path
import argparse, collections, copy, datetime, hashlib, json, sys, tarfile, zipfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[1])
parser.add_argument('--replay', type=Path, required=True)
parser.add_argument('--live', type=Path, required=True)
parser.add_argument('--upstream', type=Path, required=True)
parser.add_argument('--original-evidence', type=Path)
parser.add_argument('--historical', type=Path, help='Optional unpacked earlier development-v2 root')
parser.add_argument('--use-archived-sources', action='store_true',
                    help='Explicitly verify each source-at-freeze.zip against its manifest instead of the working tree')
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
REPO = args.repo.resolve()
UPSTREAM = args.upstream.resolve()
sys.path[:0] = [str(REPO), str(REPO/'src')]
from resimind.repair import plan_sha256
from experiments.chinatravel.recording import canonical, digest
from experiments.chinatravel.official_scoring import score
from experiments.chinatravel_repair.proposals import public_task_envelope

sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
read = lambda p: json.loads(p.read_text())
issues = []
source_verification = []

def diff(a, b, path=''):
    if isinstance(a, dict) and isinstance(b, dict):
        return [z for k in sorted(set(a)|set(b)) for z in
                (diff(a[k], b[k], path+'/'+k) if k in a and k in b else [(path+'/'+k, a.get(k), b.get(k))])]
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return [z for i, (x,y) in enumerate(zip(a,b)) for z in diff(x,y,path+'/'+str(i))]
    return [] if a == b else [(path,a,b)]

def frozen(root):
    m = read(root/'manifest.json')
    if sha(root/'manifest.json') != (root/'manifest.sha256').read_text().strip(): issues.append(str(root)+':manifest_hash')
    current_differences = [p for p,h in m['source_sha256'].items()
                           if not (REPO/p).is_file() or sha(REPO/p) != h]
    record = {'run': root.name, 'source_count': len(m['source_sha256']),
              'mode': 'archived_zip' if args.use_archived_sources else 'working_tree',
              'current_tree_differences': current_differences}
    if args.use_archived_sources:
        archive_path = root/'source-at-freeze.zip'
        record['archive_sha256'] = sha(archive_path)
        with zipfile.ZipFile(archive_path) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)): issues.append(str(root)+':duplicate_source_entries')
            for p,h in m['source_sha256'].items():
                if p not in names or hashlib.sha256(archive.read(p)).hexdigest() != h:
                    issues.append(str(root)+':archived_source:'+p)
    else:
        issues.extend('source:'+p for p in current_differences)
    source_verification.append(record)
    for p,h in m['database_sha256'].items():
        if sha((UPSTREAM/'chinatravel/environment/database')/p) != h: issues.append('database:'+p)
    return m

def audit_repair(input_plan, repaired, identity):
    if plan_sha256(input_plan) != repaired['initial_sha256']: issues.append(identity+':initial_hash')
    if plan_sha256(repaired['draft']) != repaired['draft_sha256']: issues.append(identity+':draft_hash')
    current = copy.deepcopy(input_plan)
    commits = rejected = deferred = 0
    transaction_statuses = collections.Counter()
    rejected_regressions = []
    for tx in repaired['transactions']:
        transaction_statuses[tx['result']['status']] += 1
        if tx['result']['status'] not in ('repaired','accepted'):
            rejected += tx['result']['status'] == 'rejected'
            deferred += tx['result']['status'] == 'deferred'
            rejected_regressions.extend(k for k,v in tx['result'].get('before_checks',{}).items()
                                        if v is True and tx['result'].get('after_checks',{}).get(k) is not True)
            continue
        commits += 1
        if plan_sha256(current) != tx['proposal']['base_sha256']: issues.append(identity+':stale_base')
        for edit in tx['proposal']['edits']:
            if not set(edit['evidence_refs']) <= set(repaired['evidence']): issues.append(identity+':missing_evidence')
            bits = [s.replace('~1','/').replace('~0','~') for s in edit['path'].split('/')[1:]]
            parent = current
            for key in bits[:-1]: parent = parent[int(key)] if isinstance(parent,list) else parent[key]
            key = int(bits[-1]) if isinstance(parent,list) else bits[-1]
            if parent[key] != edit['before']: issues.append(identity+':stale_edit')
            parent[key] = copy.deepcopy(edit['value'])
        if current != tx['result']['plan']: issues.append(identity+':transaction_output')
        checks_before, checks_after = tx['result']['before_checks'], tx['result']['after_checks']
        if any(v is True and checks_after.get(k) is not True for k,v in checks_before.items()): issues.append(identity+':committed_regression')
    if current != repaired['draft']: issues.append(identity+':unrecorded_mutation')
    changes = diff(input_plan, repaired['draft'])
    return {'committed_transactions': commits, 'rejected_transactions': rejected,
            'deferred_transactions': deferred, 'transaction_statuses': dict(transaction_statuses),
            'rejected_regression_checks': sorted(set(rejected_regressions)),
            'leaf_changes': len(changes),
            'edited_activity_paths': sorted({'/'.join(p.split('/')[:5]) for p,_,_ in changes}),
            'changed_fields': dict(collections.Counter(p.rsplit('/',1)[-1] for p,_,_ in changes)),
            'small_numeric_changes_le_0_1': sum(isinstance(a,(int,float)) and isinstance(b,(int,float)) and abs(a-b)<=.1 for _,a,b in changes),
            'major_world_checks_resolved': [k for k in repaired['resolved'] if k.startswith('Is_') and '/' not in k],
            'unresolved_reason_types': sorted({z['reason'] for z in repaired['unresolved']}),
            'status': repaired['status'], 'stop_reason': repaired['stop_reason']}

replay_root = args.replay.resolve()
m = frozen(replay_root)
original = args.original_evidence or REPO/'docs/evidence/chinatravel-pilot-v1'
for p,h in m['archive_sha256'].items():
    if sha(original/p) != h: issues.append('original_archive:'+p)
out_hashes = {(i['uid'],i['attempt']): i['sha256'] for i in read(replay_root/'completed.json')['outputs']}
replay_cases = []
for item in m['jobs']:
    uid,n = item['uid'],item['attempt']; p = replay_root/item['input']; inp = read(p); repaired = read(p.with_name('repair.json'))
    if sha(p) != item['input_sha256']: issues.append(uid+':input_mutated')
    if sha(p.with_name('repair.json')) != out_hashes[(uid,n)]: issues.append(uid+':repair_mutated')
    raw_path = original/'runs'/uid/'resimind_react/worker'/f'candidate_{n:02d}.json'
    if raw_path.exists():
        oldplan = read(raw_path)['plan']
    else:
        with tarfile.open(original/'archives'/f'{uid}.tar.xz', 'r:xz') as archive:
            suffix = f'{uid}/resimind_react/worker/candidate_{n:02d}.json'
            member = next(entry for entry in archive.getmembers() if entry.name.endswith(suffix))
            oldplan = json.load(archive.extractfile(member))['plan']
    if inp['plan'] != oldplan: issues.append(uid+':original_plan_changed')
    replay_cases.append({'uid':uid,'attempt':n, **audit_repair(inp['plan'],repaired,f'{uid}/{n}')})

live_root = args.live.resolve(); lm = frozen(live_root); settings = lm['settings']
if not all((live_root/'runs'/uid/'resimind_repair/result.json').is_file() for uid in lm['selected_uids']):
    raise SystemExit('Both live runs must be terminal before any gold scoring.')
frozen_unix = datetime.datetime.fromisoformat(lm['frozen_at']).timestamp()
for category in ('public','gold'):
    for uid,h in lm[f'{category}_query_sha256'].items():
        if sha(live_root/f'queries/{category}/{uid}.json') != h: issues.append(uid+':query_hash')
live_cases = []
for uid in lm['selected_uids']:
    job = live_root/'runs'/uid/'resimind_repair'; terminal = read(job/'result.json'); worker = read(job/'worker/result.json')
    query = read(live_root/f'queries/public/{uid}.json'); gold = read(live_root/f'queries/gold/{uid}.json')
    header = public_task_envelope(query)['explicit_header']; rows = [read(p) for p in sorted((job/'requests').glob('*.json'))]
    worker_requests = [json.loads(line) for line in (job/'worker/model_requests.jsonl').read_text().splitlines()]
    worker_responses = [json.loads(line) for line in (job/'worker/model_responses.jsonl').read_text().splitlines()]
    if len(worker_requests) != len(rows) or len(worker_responses) != len(rows): issues.append(uid+':worker_message_counts')
    totals = dict(api_attempts=0,prompt_bytes=0,prompt_tokens=0,completion_tokens=0,cache_hit_tokens=0,cache_miss_tokens=0,unknown_usage_requests=0)
    models = collections.Counter(); statuses = collections.Counter(); max_request_seconds = max_prompt_bytes = 0
    for serial,row in enumerate(rows,1):
        if row['request_number'] != serial: issues.append(uid+':request_sequence')
        body = row['request']; usage = row.get('usage',{}); statuses[row['status']] += 1
        if row['started_unix'] < frozen_unix: issues.append(uid+':request_before_freeze')
        if serial <= len(worker_requests):
            if worker_requests[serial-1]['request'] != serial or worker_requests[serial-1]['messages'] != body['messages']: issues.append(uid+':worker_request_mismatch')
        if serial <= len(worker_responses):
            response = worker_responses[serial-1]
            if response['request'] != serial or response['message'] != row.get('message') or response['usage'] != usage: issues.append(uid+':worker_response_mismatch')
        if digest(body) != row['request_sha256']: issues.append(uid+':request_hash')
        if row['api_attempted']:
            size = len(canonical(body['messages']).encode())
            if size != row['prompt_bytes']: issues.append(uid+':prompt_bytes')
            if size>settings['max_request_prompt_bytes'] or totals['completion_tokens']+settings['max_output_tokens']>settings['max_total_output_tokens']: issues.append(uid+':dispatch_budget')
            totals['api_attempts']+=1;totals['prompt_bytes']+=size
            for field in ('prompt_tokens','completion_tokens'):
                if type(usage.get(field)) is not int: issues.append(uid+':unknown_usage')
                totals[field]+=usage.get(field,0)
            totals['cache_hit_tokens']+=usage.get('prompt_cache_hit_tokens',0);totals['cache_miss_tokens']+=usage.get('prompt_cache_miss_tokens',0)
            totals['unknown_usage_requests']+=any(type(usage.get(k)) is not int for k in ('prompt_tokens','completion_tokens'))
            models[row.get('model_returned')]+=1
            max_request_seconds=max(max_request_seconds,row.get('elapsed_seconds',0));max_prompt_bytes=max(max_prompt_bytes,size)
        if row['totals_after']!=totals: issues.append(uid+':running_totals')
        if totals['api_attempts']>settings['max_calls'] or totals['prompt_bytes']>settings['max_total_prompt_bytes']:issues.append(uid+':cumulative_budget')
    if terminal['ledger']!=totals: issues.append(uid+':terminal_ledger')
    if terminal['elapsed_seconds']>settings['wall_seconds'] or max_request_seconds>settings['request_timeout_seconds']: issues.append(uid+':deadline')
    if worker['plan']!=terminal['plan'] or worker['status']!=terminal['status']:issues.append(uid+':worker_parent_disagree')
    wc=worker['details']['worker_usage']
    if (wc['requests'],wc['prompt_tokens'],wc['completion_tokens'])!=(totals['api_attempts'],totals['prompt_tokens'],totals['completion_tokens']):issues.append(uid+':worker_usage')
    candidates=[]
    for p in sorted((job/'worker/raw_candidates').glob('candidate_*.json')):
        raw=read(p);plan=raw.get('plan');n=raw['attempt']
        if not plan:
            candidates.append({'attempt':n,'present':False,'react_step':raw['react_step']});continue
        mismatches=[k for k in ('start_city','target_city','people_number') if plan.get(k)!=header[k]]
        if len(plan.get('itinerary',[]))!=header['days']:mismatches.append('days')
        repaired=read(job/'worker'/f'repair_{n:02d}.json')
        rawscore=score(gold,plan,upstream_root=UPSTREAM)
        draftscore=score(gold,repaired['draft'],upstream_root=UPSTREAM)
        take=lambda s:{k:s[k] for k in ('schema','env','logical','final','diagnostics')}
        candidates.append({'attempt':n,'present':True,'react_step':raw['react_step'],
                           'public_header_mismatch':mismatches,'raw_official':take(rawscore),
                           'draft_official':take(draftscore), **audit_repair(plan,repaired,f'live/{uid}/{n}')})
    finalscore=score(gold,terminal['plan'] or {},upstream_root=UPSTREAM)
    old=read(args.historical/'runs'/uid/'resimind_react/result.json') if args.historical else None
    live_cases.append({'uid':uid,'status':terminal['status'],'delivered':bool(terminal['plan']),
                       'stop_reason':terminal['details']['resimind_stop_reason'],'ledger':totals,
                       'request_statuses':dict(statuses),'returned_models':dict(models),
                       'max_request_seconds':max_request_seconds,'max_prompt_bytes':max_prompt_bytes,
                       'elapsed_seconds':terminal['elapsed_seconds'],'worker_exit_code':terminal.get('worker_exit_code'),
                       'candidates':candidates,'delivered_official':{k:finalscore[k] for k in ('schema','env','logical','final')},
                       'historical_dev_v2_delivered':bool(old['plan']) if old is not None else None})

result={'audit_type':'independent_post_run_repair_audit','generated_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'audit_script_sha256':sha(Path(__file__)),'issues':issues,'passed':not issues,
        'source_verification':source_verification,
        'replay_manifest_sha256':sha(replay_root/'manifest.json'),'live_manifest_sha256':sha(live_root/'manifest.json'),
        'replay_cases':replay_cases,'live_cases':live_cases,
        'limitations':['Replay uses seen candidates; success unchanged is not completion-rate improvement.',
                       'Local boolean check counts and field edits are not solved tasks.',
                       'Live evaluation uses two development tasks, with no concurrent baseline or causal ablation.',
                       'Gold was used only for independent offline scoring after both live runs terminated.',
                       'Worker exit -15 occurs in supervisor cleanup after receipt of terminal result; neither run hit deadline.']}
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'passed':not issues,'issues':issues,'replay_candidates':len(replay_cases),'live':[
    {k:v for k,v in c.items() if k!='candidates'}|{'raw_and_draft_scores':[(x['attempt'],x.get('raw_official',{}).get('final'),x.get('draft_official',{}).get('final')) for x in c['candidates']]} for c in live_cases]},ensure_ascii=False,indent=2))

raise SystemExit(0 if not issues else 1)
