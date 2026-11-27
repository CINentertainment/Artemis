"""Passphrase-encrypted SQLite snapshots and staged portable DPAPI recovery.

No plaintext portable database is written during backup or verification.
Snapshots are individually consistent, not a distributed four-service transaction.
"""
import base64,hashlib,io,json,os,re,sqlite3,time,zipfile
from pathlib import Path,PurePosixPath
from contextlib import closing
from uuid import uuid4
from datetime import datetime,timezone
MAGIC=b'ARTVAULT1';MAX_TOTAL=512*1024*1024

def now():return datetime.now(timezone.utc).isoformat()
def sha(data):return hashlib.sha256(data).hexdigest()
def key_for(password,salt):
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    if len(password)<16:raise ValueError('Use a recovery passphrase of at least sixteen characters.')
    return Scrypt(salt=salt,length=32,n=2**15,r=8,p=1).derive(password.encode('utf-8'))
def seal(data,password):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    salt=os.urandom(16);nonce=os.urandom(12);header=MAGIC+salt+nonce
    return header+AESGCM(key_for(password,salt)).encrypt(nonce,data,header)
def unseal(data,password):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if len(data)>MAX_TOTAL+32*1024*1024 or not data.startswith(MAGIC)or len(data)<53:raise ValueError('Invalid or oversized backup archive.')
    salt=data[9:25];nonce=data[25:37]
    return AESGCM(key_for(password,salt)).decrypt(nonce,data[37:],data[:37])
def qi(value):return '"'+value.replace('"','""')+'"'
def tables(db):return [r[0]for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
def check(db):
    if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('SQLite integrity check failed.')
    if db.execute('PRAGMA foreign_key_check').fetchone():raise ValueError('SQLite foreign-key check failed.')
    return {table:db.execute('SELECT count(*) FROM '+qi(table)).fetchone()[0]for table in tables(db)}
def snapshot(path):
    path=Path(path)
    if not path.is_file()or path.is_symlink()or path.stat().st_size>MAX_TOTAL:raise ValueError('Database is unavailable, linked, or too large.')
    memory=sqlite3.connect(':memory:');start=time.monotonic()
    def progress(status,left,total):
        if time.monotonic()-start>90:raise TimeoutError('SQLite backup exceeded ninety seconds.')
    try:
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=10))as source:source.backup(memory,pages=256,progress=progress,sleep=.05)
        counts=check(memory);return memory.serialize(),counts
    finally:memory.close()
def db_from(raw):
    db=sqlite3.connect(':memory:');db.deserialize(raw);return db

def portable_tokens(value,mapping,decode,depth=0):
    if depth>50:raise ValueError('Encrypted payload nesting exceeds the recovery bound.')
    if isinstance(value,str):
        if value.startswith('dpapi:v1:'):
            if value not in mapping:
                plain=decode(value);mapping[value]=plain;portable_tokens(plain,mapping,decode,depth+1)
        elif value.startswith(('{','[','"')):
            try:parsed=json.loads(value)
            except ValueError:return
            portable_tokens(parsed,mapping,decode,depth+1)
    elif isinstance(value,dict):
        for v in value.values():portable_tokens(v,mapping,decode,depth+1)
    elif isinstance(value,list):
        for v in value:portable_tokens(v,mapping,decode,depth+1)

def collect_tokens(db,mapping,decode):
    for table in tables(db):
        for row in db.execute('SELECT * FROM '+qi(table)):
            for value in row:portable_tokens(value,mapping,decode)

def rebind_text(value,mapping,encode,cache,stack=None):
    if not isinstance(value,str):return value
    if value.startswith('dpapi:v1:'):
        if value in cache:return cache[value]
        if value not in mapping:raise ValueError('Recovery map is missing an encrypted value.')
        stack=set()if stack is None else stack
        if value in stack or len(stack)>50:raise ValueError('Invalid encrypted value dependency.')
        result=encode(rebind_text(mapping[value],mapping,encode,cache,stack|{value}));cache[value]=result;return result
    try:parsed=json.loads(value)
    except(ValueError,TypeError):return value
    def visit(v):
        if isinstance(v,str):return rebind_text(v,mapping,encode,cache,stack)
        if isinstance(v,list):return [visit(x)for x in v]
        if isinstance(v,dict):return {k:visit(x)for k,x in v.items()}
        return v
    # JSON string scalars without DPAPI content must not recursively parse themselves.
    if isinstance(parsed,str)and not parsed.startswith('dpapi:v1:'):return value
    updated=visit(parsed)
    return json.dumps(updated,ensure_ascii=False,separators=(',',':'))if updated!=parsed else value

def rebound_database(raw,mapping,encode):
    db=db_from(raw);cache={}
    try:
        for table in tables(db):
            columns=[r[1]for r in db.execute('PRAGMA table_info('+qi(table)+')')]
            for column in columns:
                values=[r[0]for r in db.execute('SELECT DISTINCT '+qi(column)+' FROM '+qi(table)+" WHERE typeof("+qi(column)+")='text'")]
                for value in values:
                    updated=rebind_text(value,mapping,encode,cache)
                    if updated!=value:db.execute('UPDATE '+qi(table)+' SET '+qi(column)+'=? WHERE '+qi(column)+'=?',(updated,value))
        db.commit();counts=check(db);return db.serialize(),counts
    finally:db.close()

def archive_entries(data):
    entries={};size=0
    with zipfile.ZipFile(io.BytesIO(data))as archive:
        if len(archive.infolist())>10000:raise ValueError('Archive file count exceeds the limit.')
        for item in archive.infolist():
            path=Path(item.filename)
            if path.is_absolute()or '..'in path.parts or '\\'in item.filename or item.filename in entries:raise ValueError('Unsafe backup member.')
            size+=item.file_size
            if size>MAX_TOTAL:raise ValueError('Uncompressed archive exceeds the limit.')
            entries[item.filename]=archive.read(item)
    return entries

def safe_label(label):
    return isinstance(label,str)and 1<=len(label)<=200 and not any(c in label for c in ['\\',':','\x00'])and not PurePosixPath(label).is_absolute()and '..'not in PurePosixPath(label).parts and str(PurePosixPath(label))==label

def build_archive(databases,attachments,password,decode):
    entries={};manifest={'version':1,'created_at':now(),'databases':{},'files':{},'dpapi_values':0,'consistency':'Individually consistent sequential SQLite snapshots; not a distributed transaction.'};mapping={};total=0
    for label,path in databases.items():
        if not re.fullmatch(r'[a-z_]{2,24}',label):raise ValueError('Invalid database service label.')
        raw,counts=snapshot(path);total+=len(raw)
        if total>MAX_TOTAL:raise ValueError('Combined database snapshots exceed the backup size limit.')
        db=db_from(raw)
        try:collect_tokens(db,mapping,decode)
        finally:db.close()
        name='databases/'+label+'.sqlite';entries[name]=raw;manifest['databases'][label]={'member':name,'sha256':sha(raw),'tables':counts,'source_path':str(Path(path).resolve())}
    if not databases:raise ValueError('At least one database is required.')
    for label,path in attachments.items():
        if not safe_label(label):raise ValueError('Invalid attachment label.')
        path=Path(path)
        if not path.is_file()or path.is_symlink():raise ValueError('Attachment unavailable or linked.')
        if path.stat().st_size>MAX_TOTAL-total:raise ValueError('Attachment exceeds the remaining backup bound.')
        raw=path.read_bytes();total+=len(raw)
        if total>MAX_TOTAL:raise ValueError('Backup files exceed the combined size limit.')
        try:portable_tokens(raw.decode('utf-8'),mapping,decode)
        except UnicodeDecodeError:pass
        name='files/'+label;entries[name]=raw;manifest['files'][label]={'member':name,'sha256':sha(raw),'source_path':str(path.resolve())}
    manifest['dpapi_values']=len(mapping);entries['portable-map.json']=json.dumps(mapping,ensure_ascii=False).encode();entries['manifest.json']=json.dumps(manifest,ensure_ascii=False).encode()
    if sum(map(len,entries.values()))>MAX_TOTAL:raise ValueError('Snapshots plus the portable recovery map exceed the total archive bound.')
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED)as z:
        for name,raw in entries.items():z.writestr(name,raw)
    return seal(stream.getvalue(),password),manifest

def inspect_archive(raw,password,encode=None,decode=None):
    entries=archive_entries(unseal(raw,password));manifest=json.loads(entries['manifest.json']);mapping=json.loads(entries['portable-map.json'])
    if manifest.get('version')!=1 or len(mapping)!=manifest['dpapi_values']:raise ValueError('Recovery manifest mismatch.')
    for label,meta in manifest['databases'].items():
        if not re.fullmatch(r'[a-z_]{2,24}',label)or meta['member']!='databases/'+label+'.sqlite':raise ValueError('Unsafe database manifest path.')
        content=entries[meta['member']]
        if sha(content)!=meta['sha256']:raise ValueError('Database checksum mismatch.')
        db=db_from(content)
        try:
            if check(db)!=meta['tables']:raise ValueError('Row-count verification failed.')
        finally:db.close()
        if encode:
            rebound,counts=rebound_database(content,mapping,encode)
            if counts!=meta['tables']:raise ValueError('Rebound row count changed.')
            if decode:
                trial={};db=db_from(rebound)
                try:collect_tokens(db,trial,decode)
                finally:db.close()
    for label,meta in manifest['files'].items():
        if not safe_label(label)or meta['member']!='files/'+label:raise ValueError('Unsafe attachment manifest path.')
        if sha(entries[meta['member']])!=meta['sha256']:raise ValueError('Attachment checksum mismatch.')
    return manifest,entries,mapping

def stage_restore(raw,password,output,encode,decode=None):
    output=Path(output)
    if output.exists():raise ValueError('Choose a NEW, nonexistent restore rehearsal folder. Existing files are never overwritten.')
    manifest,entries,mapping=inspect_archive(raw,password,encode=encode,decode=decode)
    output.mkdir(parents=True);report={'created_at':now(),'source_created_at':manifest['created_at'],'services':{},'warning':'Staged only. Live applications and settings have not been changed.'}
    try:
        for label,meta in manifest['databases'].items():
            rebound,counts=rebound_database(entries[meta['member']],mapping,encode);target=output/(label+'.sqlite');target.write_bytes(rebound)
            report['services'][label]={'file':target.name,'tables':counts,'sha256':sha(rebound),'source_path':meta['source_path']}
        for label,meta in manifest['files'].items():
            target=output/'files'/label;target.parent.mkdir(parents=True,exist_ok=True);content=entries[meta['member']]
            try:content=rebind_text(content.decode('utf-8'),mapping,encode,{}).encode('utf-8')
            except UnicodeDecodeError:pass
            target.write_bytes(content)
        (output/'restore-report.json').write_text(json.dumps(report,indent=2),encoding='utf-8');return report
    except Exception:
        # Retain failed output for inspection; never label it a successful rehearsal.
        (output/'RESTORE-FAILED.txt').write_text('Restore staging failed. Do not activate these files.');raise
