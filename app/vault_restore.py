"""Offline/staged restore utility. Passphrases are prompted, never CLI arguments."""
import argparse,getpass,json,socket,sqlite3,shutil,os
from contextlib import closing
from pathlib import Path
from uuid import uuid4
from app.services import continuity_core as core
from app.services.encryption import protect_text,unprotect_text

def stopped():
    for port in [8200,8201,8000,8001,8003]:
        with socket.socket()as s:
            s.settimeout(.3)
            if s.connect_ex(('127.0.0.1',port))==0:raise ValueError('Stop Artemis, its remote gateway and all three companion services before activation.')
def activate(stage,service,target):
    stopped();stage=Path(stage);report=json.loads((stage/'restore-report.json').read_text());meta=report['services'][service];source=stage/meta['file'];original_target=Path(target)
    if original_target.is_symlink():raise ValueError('Refusing a linked live database target.')
    target=original_target.resolve()
    if not target.is_file()or target.is_symlink():raise ValueError('Activation requires an existing explicit target database.')
    raw=source.read_bytes()
    if core.sha(raw)!=meta['sha256']:raise ValueError('Staged database changed after verification.')
    old,old_counts=core.snapshot(target);newdb=core.db_from(raw);old_db=core.db_from(old)
    try:
        if core.check(newdb)!=meta['tables']:raise ValueError('Staged table counts differ from the restore report.')
        schema=lambda db:sorted(db.execute("SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' AND sql IS NOT NULL").fetchall())
        if schema(newdb)!=schema(old_db):raise ValueError('Schema differs. Restore matching application code before activating this database.')
    finally:newdb.close();old_db.close()
    # Require exclusive access and checkpoint WAL before replacing the live file.
    with closing(sqlite3.connect(target,timeout=1))as db:
        check=db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
        if check and check[0]:raise ValueError('Database still has active WAL users.')
        db.execute('PRAGMA journal_mode=DELETE');db.execute('BEGIN EXCLUSIVE');db.commit()
    backup=target.with_name(target.name+'.before-restore-'+uuid4().hex+'.sqlite');
    with backup.open('xb')as f:f.write(old);f.flush();os.fsync(f.fileno())
    if core.sha(backup.read_bytes())!=core.sha(old):raise ValueError('Pre-restore snapshot verification failed.')
    temp=target.with_name(target.name+'.restore-'+uuid4().hex)
    try:
        with temp.open('xb')as f:f.write(raw);f.flush();os.fsync(f.fileno())
        for suffix in ['-wal','-shm']:
            side=Path(str(target)+suffix)
            if side.exists()and side.stat().st_size:raise ValueError('Database sidecars remain; activation refused.')
            side.unlink(missing_ok=True)
        temp.replace(target)
    finally:temp.unlink(missing_ok=True)
    return {'activated':str(target),'pre_restore_backup':str(backup),'warning':'Settings/report files remain staged; review and copy them separately before normal startup.'}
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--archive',type=Path);parser.add_argument('--output',type=Path);parser.add_argument('--verify',action='store_true');parser.add_argument('--activate',choices=['artemis','notes','risk_tracker','market_lens']);parser.add_argument('--stage',type=Path);parser.add_argument('--target',type=Path);parser.add_argument('--confirm-activate',action='store_true');args=parser.parse_args()
    if args.activate:
        if not args.confirm_activate or not args.stage or not args.target:parser.error('Activation needs --stage, --target and --confirm-activate.')
        print(json.dumps(activate(args.stage,args.activate,args.target),indent=2));return
    if not args.archive:parser.error('--archive is required.')
    password=getpass.getpass('Recovery passphrase: ')
    if args.verify:
        manifest,_,_=core.inspect_archive(args.archive.read_bytes(),password,encode=protect_text,decode=unprotect_text);print('PASS: archive authentication, database integrity and current-account DPAPI rebind rehearsal.',manifest['created_at']);return
    if not args.output:parser.error('Use --output with a NEW folder for staged restore.')
    report=core.stage_restore(args.archive.read_bytes(),password,args.output,protect_text,unprotect_text);print(json.dumps(report,indent=2))
if __name__=='__main__':main()
