"""Managed downloads and reversible configuration for official upstream components.
No networking or privileged actions happen at import time.
"""
from __future__ import annotations
import hashlib
import json
import os
import re
import shutil
import sys
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
import urllib.error
import zipfile
from pathlib import Path

ROOT = Path(os.environ.get('LOCALAPPDATA') or Path.home()) / 'NetPilot'
SETTINGS = ROOT / 'settings-v2.json'
COMPONENTS = ROOT / 'components'
REPOS = {'zapret': 'Flowseal/zapret-discord-youtube', 'telegram': 'Flowseal/tg-ws-proxy'}
HOSTS = {'github.com', 'api.github.com', 'objects.githubusercontent.com',
         'release-assets.githubusercontent.com', 'github-releases.githubusercontent.com'}
ASSETS = Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent.parent)) / 'assets'
DOMAINS_FILE = ASSETS / 'domains.txt'
CLOUDFLARE_FILE = ASSETS / 'cloudflare-domains.txt'
DEFAULT = {'components_dir': str(COMPONENTS), 'strategy':'general.bat',
           'auto_check': True, 'quiet_games': True, 'auto_apply': True, 'auto_upgrade': False, 'start_hidden': False, 'installed':{}, 'language':'ru', 'telegram_secret':''}


def read_cfg():
    config = dict(DEFAULT, installed={})
    try:
        obj = json.loads(SETTINGS.read_text(encoding='utf-8'))
        if isinstance(obj, dict):
            for k in DEFAULT:
                if k in obj and isinstance(obj[k], type(DEFAULT[k])): config[k] = obj[k]
    except (OSError, ValueError): pass
    return config


def write_cfg(config):
    ROOT.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS.with_suffix('.tmp')
    tmp.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(SETTINGS)


def base_dir(config):
    return Path(config['components_dir']).expanduser().resolve()


def sub_dir(config, kind):
    return base_dir(config) / kind


def fetch_bytes(url, max_bytes=600_000_000, timeout=25, progress=None):
    req = urllib.request.Request(url, headers={'User-Agent':'NetPilot/0.5',
                      'Accept':'application/vnd.github+json' if url.startswith('https://api.github.com/') else '*/*'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        host = urllib.parse.urlparse(r.geturl()).hostname or ''
        if host not in HOSTS and not host.endswith('.githubusercontent.com'):
            raise ValueError('Redirect to unexpected download host: ' + host)
        buf = bytearray()
        advertised = int(r.headers.get('Content-Length', '0') or '0')
        while True:
            chunk = r.read(1024*1024)
            if not chunk:break
            buf.extend(chunk)
            if len(buf)>max_bytes:raise ValueError('Download exceeds safety limit')
            if progress and advertised:
                progress(min(90,round(90*len(buf)/advertised)), f'Скачивание: {len(buf)//(1024*1024)} / {advertised//(1024*1024)} МБ')
        return bytes(buf)


def release(kind):
    if kind not in REPOS: raise ValueError('Unknown tool')
    repo=REPOS[kind]
    info=json.loads(fetch_bytes(f'https://api.github.com/repos/{repo}/releases/latest',3_000_000).decode('utf-8'))
    if not info.get('tag_name') or not info.get('assets'): raise RuntimeError('Release has no usable files')
    assets=info['assets']
    if kind=='zapret':
        c=[a for a in assets if a['name'].lower().endswith('.zip') and not any(s in a['name'].lower() for s in ('source','src','debug','symbols'))]
    else:
        c=[a for a in assets if a['name'].lower()=='tgwsproxy_windows.exe']
    if not c: raise RuntimeError('Suitable Windows x64 release asset is not published')
    asset=max(c, key=lambda a:a.get('size',0)) if kind=='zapret' else c[0]
    return {'kind':kind,'tag':str(info['tag_name']), 'name':asset['name'],
            'size':asset.get('size',0),'url':asset['browser_download_url'],
            'digest':asset.get('digest'), 'release_url':info.get('html_url','')}


def check_digest(data, expected):
    if expected:
        algo,_,want=expected.partition(':')
        if algo.lower()!='sha256' or not want:raise ValueError('Unsupported release digest')
        if hashlib.sha256(data).hexdigest().lower()!=want.lower():raise ValueError('SHA256 mismatch')
    return hashlib.sha256(data).hexdigest()


def safe_unpack(payload, destination):
    destination.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(__import__('io').BytesIO(payload)) as z:
        entries=z.infolist()
        if len(entries)>6000 or sum(i.file_size for i in entries)>900_000_000:raise ValueError('Archive exceeds extraction limits')
        for i in entries:
            name=i.filename.replace('\\','/')
            parts=Path(name).parts
            if (name.startswith('/') or ':' in name or '..' in parts or
                any(re.fullmatch(r'(?i)(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?',part) for part in parts)):
                raise ValueError('Unsafe archive path')
            if (i.external_attr >> 16) & 0o170000 == 0o120000:raise ValueError('Symlink in archive')
        z.extractall(destination)


def _locate_zapret(staging):
    found=list(staging.rglob('service.bat'))
    valid=[p.parent for p in found if (p.parent/'bin').is_dir() and any(p.parent.glob('general*.bat'))]
    if len(valid)!=1: raise ValueError('Downloaded archive lacks expected Zapret directory structure')
    return valid[0]


def merge_domains(zapret_dir, include_extended=False):
    target=Path(zapret_dir)/'lists'/'list-general-user.txt'
    target.parent.mkdir(parents=True,exist_ok=True)
    existing=target.read_text(encoding='utf-8-sig',errors='replace').splitlines() if target.exists() else []
    out=list(existing)
    existing_set={s.strip().lower() for s in existing}
    clean=[]; bad=[]
    lines = CLOUDFLARE_FILE.read_text(encoding='utf-8').splitlines()
    if include_extended:
        lines += DOMAINS_FILE.read_text(encoding='utf-8').splitlines()
    for line in lines:
        name=line.strip().lower()
        if not name or name.startswith('#'):continue
        if not re.fullmatch(r'[a-z0-9-]+(?:\.[a-z0-9-]+)+',name):bad.append(name);continue
        if name not in existing_set:
            clean.append(name);existing_set.add(name)
    if clean:
        if target.exists():shutil.copy2(target,target.with_name(target.name+'.netpilot-backup'))
        out.extend(clean)
        target.write_text('\n'.join(out).rstrip()+'\n',encoding='utf-8')
    return len(clean),bad


def install_component(config, meta, progress=None):
    """Install a selected, pinned release with local staging and rollback. Caller must stop running tools first."""
    kind=meta['kind'];dest=sub_dir(config,kind)
    dest.parent.mkdir(parents=True,exist_ok=True)
    payload=fetch_bytes(meta['url'],progress=progress);digest=check_digest(payload,meta.get('digest'))
    if progress:progress(93,'Проверка и распаковка…')
    if kind=='telegram' and (not payload.startswith(b'MZ') or len(payload)<500_000):
        raise ValueError('Invalid Windows executable payload')
    with tempfile.TemporaryDirectory(prefix='.netpilot-',dir=str(dest.parent)) as td:
        staging=Path(td)
        if kind=='zapret':
            unpack=staging/'unpacked'; safe_unpack(payload,unpack)
            source=_locate_zapret(unpack)
        else:
            source=staging/'data'; source.mkdir()
            (source/'TgWsProxy_windows.exe').write_bytes(payload)
            (source/'TgWsProxy_data').mkdir() # portable settings independent of machine
        new=dest.with_name(dest.name+'.new')
        if new.exists():shutil.rmtree(new)
        shutil.copytree(source,new)
        if kind=='zapret' and dest.is_dir():
            # Preserve the user's Flowseal custom lists and settings through upstream updates.
            for f in ('lists/list-general-user.txt','lists/list-exclude-user.txt','lists/ipset-exclude-user.txt',
                      'utils/game_filter.enabled','utils/check_updates.enabled'):
                p=dest/f
                if p.is_file():
                    (new/f).parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,new/f)
            for name in ('ACTIVE_GAME_UDP.bin','ACTIVE_DISCORD_UDP.bin'):
                p=dest/'bin'/name
                if p.is_file():shutil.copy2(p,new/'bin'/name)
        elif kind=='telegram' and dest.is_dir() and (dest/'TgWsProxy_data').is_dir():
            shutil.rmtree(new/'TgWsProxy_data',ignore_errors=True)
            shutil.copytree(dest/'TgWsProxy_data',new/'TgWsProxy_data')
        if kind=='zapret':merge_domains(new)
        backup=dest.with_name(dest.name+'.previous')
        if backup.exists():shutil.rmtree(backup)
        if dest.exists(): dest.rename(backup)
        try:new.rename(dest)
        except Exception:
            if backup.exists() and not dest.exists():backup.rename(dest)
            raise
        config.setdefault('installed',{})[kind]={'tag':meta['tag'],'sha256':digest,'asset':meta['name']}
        write_cfg(config)
        if progress:progress(100,'Компонент установлен')
        return str(dest)


def rollback_component(config,kind):
    dest=sub_dir(config,kind);old=dest.with_name(dest.name+'.previous')
    if not old.exists():raise FileNotFoundError('No previous version available')
    temp=dest.with_name(dest.name+'.rollback-temp')
    if temp.exists():shutil.rmtree(temp)
    if dest.exists():dest.rename(temp)
    try:old.rename(dest)
    except Exception:
        if temp.exists():temp.rename(dest)
        raise
    if temp.exists():shutil.rmtree(temp)
    config.setdefault('installed',{}).pop(kind,None);write_cfg(config)


def list_strategies(folder):
    return sorted((p.name for p in Path(folder).glob('general*.bat') if p.is_file()),key=str.casefold)


def warp_cli():
    path=shutil.which('warp-cli')
    if path:return path
    return next((str(p) for p in [Path(os.environ.get('ProgramFiles','C:/Program Files'))/'Cloudflare'/'Cloudflare WARP'/'warp-cli.exe'] if p.is_file()),None)


def cli_run(argv,timeout=15):
    return subprocess.run(argv,timeout=timeout,capture_output=True,text=True,errors='replace',
                          creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))


def warp_action(action):
    if action not in ('status','connect','disconnect'):raise ValueError('Unknown WARP operation')
    cli=warp_cli()
    if not cli:raise FileNotFoundError('Install Cloudflare WARP using official installer')
    res=cli_run([cli,action],35 if action!='status' else 8)
    if res.returncode:raise RuntimeError((res.stderr or res.stdout).strip()[:500])
    return (res.stdout or res.stderr).strip() or 'OK'


def warp_install_command():
    if not shutil.which('winget'):raise RuntimeError('Winget is not installed')
    return ['winget','install','--id','Cloudflare.Warp','--exact','--source','winget']


def service_state(name):
    if os.name!='nt':return 'not_windows'
    res=cli_run(['sc.exe','query',name],6)
    if res.returncode:return 'not_installed'
    match=re.search(r'\b(?:RUNNING|STOPPED|START_PENDING|STOP_PENDING)\b',res.stdout)
    return match.group(0) if match else 'unknown'


def zapret_options(folder):
    base=Path(folder)
    f=base/'utils'/'game_filter.enabled'
    mode='disabled'
    if f.is_file():
        data=f.read_text(errors='replace')
        mode=re.search(r'^mode=(disabled|all|tcp|udp)$',data,re.M|re.I)
        mode=mode.group(1).lower() if mode else 'disabled'
    upd=(base/'utils'/'check_updates.enabled').exists()
    ipset=base/'lists'/'ipset-all.txt'
    if not ipset.exists():state='unknown'
    else:
        content=ipset.read_text(errors='replace').strip()
        state='any' if not content else 'none' if '203.0.113.113/32' in content else 'loaded'
    return {'game':mode,'update':upd,'ipset':state}


def set_game_filter(folder,mode):
    if mode not in ('all','tcp','udp','disabled'):raise ValueError('Invalid game filter')
    p=Path(folder)/'utils'/'game_filter.enabled'; p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(f'mode={mode}\ntcp=1024-65535\nudp=1024-65535\n',encoding='ascii')


def set_upstream_check(folder,enabled):
    p=Path(folder)/'utils'/'check_updates.enabled';p.parent.mkdir(parents=True,exist_ok=True)
    if enabled:p.write_text('ENABLED\n',encoding='ascii')
    else:p.unlink(missing_ok=True)


def set_ipset_mode(folder,mode):
    if mode not in ('loaded','none','any'):raise ValueError('Invalid IPSet mode')
    f=Path(folder)/'lists'/'ipset-all.txt';backup=f.with_name(f.name+'.backup')
    if not f.exists():raise FileNotFoundError('IPSet list missing')
    current=zapret_options(folder)['ipset']
    if mode==current:return
    if mode=='loaded':
        if backup.exists():shutil.copy2(backup,f)
        elif current!='loaded':raise RuntimeError('No IPSet backup: update list first')
    else:
        if current=='loaded':shutil.copy2(f,backup)
        f.write_text('203.0.113.113/32\n' if mode=='none' else '',encoding='ascii')


def update_ipset(folder):
    url='https://raw.githubusercontent.com/Flowseal/zapret-discord-youtube/refs/heads/main/.service/ipset-service.txt'
    # This domain is allowed only for the specific pinned upstream list.
    req=urllib.request.Request(url,headers={'User-Agent':'NetPilot/0.5'})
    with urllib.request.urlopen(req,timeout=20) as r: payload=r.read(5_000_000)
    text=payload.decode('utf-8-sig')
    if '<html' in text.lower() or len(text)<50:raise ValueError('Unexpected IPSet payload')
    f=Path(folder)/'lists'/'ipset-all.txt';f.parent.mkdir(parents=True,exist_ok=True)
    if f.is_file():shutil.copy2(f,f.with_suffix(f.suffix+'.netpilot-backup'))
    f.write_text(text,encoding='utf-8')
    return sum(bool(x.strip()) for x in text.splitlines())


def ping_web(target,timeout=8):
    start=time.monotonic()
    req=urllib.request.Request('https://'+target+'/',headers={'User-Agent':'NetPilot/0.5 network check'})
    try:
        with urllib.request.urlopen(req,timeout=timeout) as r:status=r.status
    except urllib.error.HTTPError as e:status=e.code # reachable, but server denied request
    return target,status,round(1000*(time.monotonic()-start))


def replace_active_fake(folder,which,source_name):
    """Replace one official upstream fake-packet sample, preserving the previous file."""
    mapping={'discord':'ACTIVE_DISCORD_UDP.bin','game':'ACTIVE_GAME_UDP.bin'}
    if which not in mapping:raise ValueError('Unknown fake kind')
    srcname=Path(source_name).name
    if srcname!=source_name or not srcname.lower().endswith('.bin') or srcname.upper().startswith('ACTIVE_'):
        raise ValueError('Invalid fake filename')
    base=Path(folder)/'bin'
    source=base/srcname;target=base/mapping[which]
    if not source.is_file():raise FileNotFoundError(srcname)
    if target.is_file():shutil.copy2(target,target.with_name(target.name+'.netpilot-backup'))
    shutil.copy2(source,target)
    return target.name

def fake_candidates(folder):
    base=Path(folder)/'bin'
    return sorted(p.name for p in base.glob('*.bin') if p.is_file() and not p.name.upper().startswith('ACTIVE_'))


def install_warp_silently():
    """Hide the winget console, not Windows security prompts.

    `--silent` is a request to the package's installer and isn't guaranteed to
    be honored by every WARP release. Never auto-accept driver UAC prompts.
    """
    command=warp_install_command()+['--silent','--accept-package-agreements',
                                    '--accept-source-agreements','--disable-interactivity']
    result=cli_run(command,timeout=900)
    if result.returncode:
        raise RuntimeError('Установка Cloudflare WARP: '+(result.stderr or result.stdout or
                          f'код {result.returncode}')[-700:])
    return (result.stdout or 'Установка завершена').strip()
