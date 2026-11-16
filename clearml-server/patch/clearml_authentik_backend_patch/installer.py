#!/usr/bin/env python3
import datetime,hashlib,json,pathlib,shutil,subprocess,sys
ROOT=pathlib.Path(__file__).resolve().parent
ROLE="backend"
def log(status,msg): print(f'[{status}] {msg}',flush=True)
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None
def git(repo,*args): return subprocess.run(['git','apply',*args,str(ROOT/(ROLE+'.patch'))],cwd=repo,text=True,capture_output=True)
def main():
 if len(sys.argv)!=3 or sys.argv[1] not in ('--check','--apply','--rollback'):
  print(f'Usage: bash apply.sh --check|--apply|--rollback PATH_TO_{ROLE.upper()}_REPO');return 2
 mode=sys.argv[1];repo=pathlib.Path(sys.argv[2]).expanduser().resolve()
 if not repo.is_dir(): log('FAIL',f'Directory missing: {repo}');return 1
 specs=json.loads((ROOT/'manifest.json').read_text()); failures=[]
 for rel,info in specs.items():
  expected=info['patched_sha256'] if mode=='--rollback' else info['original_sha256']
  if sha(repo/rel)!=expected:
   failures.append(rel);log('FAIL',f'{rel}: unexpected SHA256 or file missing (possible local changes/already patched)')
  else: log('PASS',f'{rel}: source hash matches')
 result=git(repo,*(['--reverse','--check'] if mode=='--rollback' else ['--check']))
 if result.returncode:failures.append('git apply');log('FAIL',f'git apply check: {result.stderr.strip()}')
 else: log('PASS','git apply check')
 if failures:log('BLOCKED',f'{len(failures)} check(s) failed; no changes made');return 1
 if mode=='--check':log('READY','Compatible; no changes made');return 0
 if mode=='--apply':
  backup=ROOT/('backup_'+datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
  for rel,info in specs.items():
   if info['original_sha256']:
    dest=backup/rel;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(repo/rel,dest)
  log('PASS',f'Original files backed up under {backup}')
 result=git(repo,*(['--reverse'] if mode=='--rollback' else []))
 if result.returncode:log('FAIL',f'git apply: {result.stderr.strip()}');return 1
 log('PASS',f'{ROLE} patch {"reversed" if mode=="--rollback" else "applied"}')
 for rel,info in specs.items():
  expected=info['original_sha256'] if mode=='--rollback' else info['patched_sha256']
  if sha(repo/rel)!=expected:log('FAIL',f'{rel}: post-patch mismatch');return 1
  log('PASS',f'{rel}: post-patch hash')
 log('RESULT','SUCCESS; source only, not rebuilt/deployed or tested against Authentik');return 0
if __name__=='__main__':sys.exit(main())
