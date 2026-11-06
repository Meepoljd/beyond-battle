"""Build a source-only ZIP with a SHA-256 manifest; excludes DBs and credentials."""
import hashlib
import json
from pathlib import Path
import zipfile

ROOT=Path(__file__).resolve().parents[1]


def package():
    directories=['server','web','scenarios','tests','contracts','scripts','docs','config']
    files=[ROOT/p for p in ['README.md','AGENTS.md','.gitignore','.env.example','requirements.lock','requirements-dev.lock','data/.gitkeep']]
    for directory in directories:
        files.extend(p for p in (ROOT/directory).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix not in ('.pyc','.sqlite3'))
    files=sorted(set(files))
    output=ROOT/'dist/beyond-battle-demo-0.1.0.zip';output.parent.mkdir(exist_ok=True)
    manifest={str(p.relative_to(ROOT)):{'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size} for p in files}
    with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
        for p in files:
            info=zipfile.ZipInfo('beyond-battle-demo/'+str(p.relative_to(ROOT)),date_time=(2026,11,1,0,0,0))
            info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=(0o100755 if p.name=='run.sh' else 0o100644)<<16
            archive.writestr(info,p.read_bytes())
        archive.writestr('beyond-battle-demo/MANIFEST.json',json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'path':str(output),'files':len(files),'bytes':output.stat().st_size,'sha256':hashlib.sha256(output.read_bytes()).hexdigest()}))


if __name__=='__main__':package()
