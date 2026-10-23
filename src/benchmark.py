"""Reversible, full-hostlist HTTPS strategy benchmark for official Flowseal releases.

No global winws killing, no service mutation, no unsigned secondary downloads.
HTTPS reachability is not a UDP/voice/video test. Benchmarks are network/ISP specific.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from pathlib import Path
import json
import os
import re
import subprocess
import tempfile
import threading
import time
import psutil
import shutil
import urllib.parse
import managed
from catalog import load_catalog, groups

TEST_HOSTS = ()  # Targets are loaded from installed Flowseal; no hardcoded four hosts.
MAX_WORKERS = 16
PER_HOST_TIMEOUT = 3.0

class BenchmarkError(RuntimeError):
    pass


def is_admin():
    if os.name != 'nt':return False
    import ctypes
    return bool(ctypes.windll.shell32.IsUserAnAdmin())


def preflight(folder):
    folder=Path(folder).resolve()
    if os.name!='nt':raise BenchmarkError('Стратегии можно запускать только в Windows.')
    if not is_admin():raise BenchmarkError('Для WinDivert нужны права администратора.')
    if managed.service_state('zapret') != 'not_installed':
        raise BenchmarkError('Обнаружена служба zapret. NetPilot не изменяет установленную службу в ходе теста. Остановите и удалите её вручную через Flowseal.')
    if any((p.info.get('name') or '').lower()=='winws.exe' for p in psutil.process_iter(['name'])):
        raise BenchmarkError('Уже запущен winws.exe. NetPilot не будет останавливать чужой процесс.')
    if not (folder/'bin'/'winws.exe').is_file():
        raise BenchmarkError('Не найден bin/winws.exe.')
    scripts=[folder/name for name in managed.list_strategies(folder)]
    if not scripts:raise BenchmarkError('Не найдены general*.bat.')
    load_catalog(folder)
    return [str(p) for p in scripts]


def prepare_hidden_strategy(source):
    content=source.read_bytes()
    replacement, count=re.subn(rb'start\s+"zapret:\s+%~n0"\s+/min\s+', b'start "" /b ',content, count=1, flags=re.I)
    if count!=1 or b'winws.exe' not in replacement.lower():
        raise BenchmarkError(f'Неизвестная схема запуска: {source.name}. Обновите NetPilot.')
    fd,name=tempfile.mkstemp(prefix='.netpilot-probe-',suffix='.bat',dir=str(source.parent))
    try:
        with os.fdopen(fd,'wb') as f:f.write(replacement)
    except Exception:
        Path(name).unlink(missing_ok=True)
        raise
    return Path(name)


def list_winws(folder, start_time):
    expected=os.path.normcase(str((folder/'bin'/'winws.exe').resolve()))
    found=[]
    for p in psutil.process_iter(['pid','name','exe','create_time']):
        try:
            info=p.info
            if ((info.get('name') or '').lower()=='winws.exe' and info.get('exe')
                and os.path.normcase(os.path.realpath(info['exe']))==expected
                and float(info.get('create_time') or 0) >= start_time-1):
                found.append(p)
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):pass
    return found


def stop_ours(procs):
    for p in procs:
        try:p.terminate()
        except psutil.NoSuchProcess:pass
    _,alive=psutil.wait_procs(procs,timeout=3)
    for p in alive:
        try:p.kill()
        except psutil.NoSuchProcess:pass
    psutil.wait_procs(alive,timeout=2)


def launch_hidden(folder, source):
    tmp=prepare_hidden_strategy(source)
    t0=time.time();cmd=None
    env=os.environ.copy();env['NO_UPDATE_CHECK']='1'
    try:
        cmd=subprocess.Popen(['cmd.exe','/D','/C',str(tmp)],cwd=str(folder),env=env,
            stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        for _ in range(35):
            ours=list_winws(folder,t0)
            if ours:return ours
            if cmd.poll() is not None and cmd.returncode not in (0,None):break
            time.sleep(.2)
        raise BenchmarkError(f'{source.name}: winws не запустился. Проверьте WinDivert и антивирус.')
    finally:
        if cmd:
            try:cmd.wait(timeout=1)
            except subprocess.TimeoutExpired:
                try:cmd.terminate()
                except OSError:pass
        tmp.unlink(missing_ok=True)


def parse_flowseal_targets(folder):
    """Read Flowseal's actual utils/targets.txt without executing PowerShell.

    The upstream standard suite uses named HTTPS targets and PING: hosts; our
    extended check additionally visits each explicit host from lists/.
    """
    targets_file = Path(folder) / 'utils' / 'targets.txt'
    targets = []
    if targets_file.is_file():
        for line in targets_file.read_text(encoding='utf-8-sig', errors='replace').splitlines():
            match = re.match(r'^\s*([\w .-]+)\s*=\s*"(https?://[^" ]+|PING:[^" ]+)"\s*$', line)
            if not match:
                continue
            name, url = match.groups()
            if url.startswith('PING:'):
                continue  # ICMP isn't a reliable indicator of HTTPS availability
            parsed = urllib.parse.urlparse(url)
            if parsed.hostname:
                targets.append((name, parsed.hostname.lower(), url))
    return targets


def curl_probe(host, *, url=None, tls='http1', timeout=5.0):
    """Match Flowseal's curl -I transport approach, never urllib proxy settings.

    Success is curl exit=0 (including HTTP 403/404). A nonzero exit becomes
    diagnostic data rather than being misreported as an inaccessible service.
    """
    curl = shutil.which('curl.exe' if os.name == 'nt' else 'curl')
    if not curl:
        return {'host':host, 'ok':False, 'ms':None, 'error':'curl.exe не найден', 'exit':127}
    address = url or 'https://' + host + '/'
    cmd = [curl, '--head', '--silent', '--show-error',
           '--max-time', str(int(timeout)), '--connect-timeout', str(min(3,int(timeout))),
           '--output', 'NUL' if os.name == 'nt' else '/dev/null',
           '--write-out', '%{http_code} %{time_total}']
    if tls == 'tls12':cmd += ['--tlsv1.2','--tls-max','1.2']
    elif tls == 'tls13':cmd += ['--tlsv1.3','--tls-max','1.3']
    else:cmd += ['--http1.1']
    cmd += [address]
    try:
        result = subprocess.run(cmd,capture_output=True,text=True,encoding='utf-8',errors='replace',
                                timeout=timeout+2,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        parts = result.stdout.strip().split()
        status = int(parts[0]) if parts and parts[0].isdigit() else 0
        elapsed = round(float(parts[1])*1000) if len(parts)>1 else None
        return {'host':host,'ok':result.returncode==0 and status!=0,
                'ms':elapsed,'status':status,'exit':result.returncode,'tls':tls,
                'error':(result.stderr.strip()[-180:] if result.returncode else '')}
    except (OSError,ValueError,subprocess.TimeoutExpired) as exc:
        return {'host':host,'ok':False,'ms':None,'tls':tls,'error':str(exc)[:150], 'exit':-1}


def probe_host(host):
    # Keep the single-host API for existing tests and full hostlist comparisons.
    return curl_probe(host)


def probe_flowseal_set(folder, hosts, cancel, progress=None):
    """All explicit hostlist hosts plus Flowseal standard HTTP/TLS targets.

    Extended hosts are tested once, upstream targets are tested in 3 modes.
    Coverage is always reported honestly, and all attempts count as work.
    """
    named = parse_flowseal_targets(folder)
    probes = [('hostlist', host, None, 'http1') for host in hosts]
    for name, host, url in named:
        probes.extend((name,host,url,mode) for mode in ('http1','tls12','tls13'))
    def execute(item):
        name,host,url,mode = item
        result = curl_probe(host,url=url,tls=mode)
        result['source']=name
        return result
    return scan_items(probes,execute,cancel,progress)


def scan_items(items, fn, cancel, progress=None, max_workers=MAX_WORKERS):
    """Bounded in-flight work with per-completed-request progress."""
    items=tuple(items)
    results=[None]*len(items)
    if not items:return []
    executor=ThreadPoolExecutor(max_workers=max_workers,thread_name_prefix='NetPilotCurl')
    inflight={}; next_index=0; done=0
    try:
        while next_index<len(items) or inflight:
            if cancel.is_set():raise BenchmarkError('Проверка отменена.')
            while next_index<len(items) and len(inflight)<max_workers:
                task=executor.submit(fn,items[next_index]); inflight[task]=next_index;next_index+=1
            completed,_=wait(tuple(inflight),timeout=.25,return_when=FIRST_COMPLETED)
            for future in completed:
                idx=inflight.pop(future)
                try:results[idx]=future.result()
                except Exception as exc:results[idx]={'host':str(items[idx]),'ok':False,'ms':None,'error':str(exc)[:90]}
                done+=1
                if progress:progress(done,len(items))
        return results
    finally:
        executor.shutdown(wait=False,cancel_futures=True)


def score(probes):
    passed=sum(x['ok'] for x in probes)
    delay=[p['ms'] for p in probes if p['ok'] and p['ms'] is not None]
    return passed,round(sum(delay)/len(delay)) if delay else None


def choose_best(rows):
    good=[r for r in rows if r.get('passed',0)>0 and not r.get('error')]
    return max(good,key=lambda r:(r['passed'],-(r['latency_ms'] if r['latency_ms'] is not None else 999999)),default=None)


def scan_hosts(hosts,cancel,progress=None,max_workers=MAX_WORKERS):
    """Bounded concurrent requests, completing per-host progress and respecting cancel.

    Never issues more than max_workers outstanding requests. Results preserve host order.
    """
    hosts=tuple(hosts)
    results=[None]*len(hosts)
    if not hosts:return []
    executor=ThreadPoolExecutor(max_workers=max_workers,thread_name_prefix='NetPilotProbe')
    inflight={};next_index=0;done=0
    try:
        while next_index<len(hosts) or inflight:
            if cancel.is_set():raise BenchmarkError('Проверка отменена. Текущая стратегия будет остановлена.')
            while next_index<len(hosts) and len(inflight)<max_workers:
                task=executor.submit(probe_host,hosts[next_index])
                inflight[task]=next_index;next_index+=1
            completed,_=wait(tuple(inflight),timeout=.25,return_when=FIRST_COMPLETED)
            for future in completed:
                idx=inflight.pop(future)
                try:results[idx]=future.result()
                except Exception as e:results[idx]={'host':hosts[idx],'ok':False,'ms':None,'error':str(e)[:80]}
                done+=1
                if progress:progress(done,len(hosts))
        return results
    finally:
        executor.shutdown(wait=True,cancel_futures=True)


def benchmark(folder,notify,cancel,*,keep_best=False,owned_pids=(),previous_strategy="general.bat"):
    """Test only our own processes, restoring prior state on failure/cancel.

    Does not stop third-party winws, modify services, or rewrite Flowseal files.
    """
    folder=Path(folder).resolve()
    original_pids=tuple(owned_pids)
    original_was_running=bool(original_pids)
    files=[];results=[];best=None;active=[];activation_error=''
    try:
        if original_was_running:
            notify(0, 'Временно остановлена стратегия NetPilot; настройки будут восстановлены')
            stop_managed_pids(original_pids,folder)
            time.sleep(.45)
        files=[Path(p) for p in preflight(folder)]
        catalog=load_catalog(folder)
        hosts=catalog.hosts
        named=parse_flowseal_targets(folder)
        steps_per_strategy=len(hosts)+3*len(named)
        if not steps_per_strategy:raise BenchmarkError('Нет адресов для проверки.')
        total_steps=len(files)*steps_per_strategy
        notify(0,f'{len(files)} стратегий · {len(hosts)} доменов · {len(named)} целей Flowseal')
        for index, source in enumerate(files):
            if cancel.is_set():raise BenchmarkError('Проверка отменена.')
            progress_base=index*steps_per_strategy
            notify(int(100*progress_base/total_steps),f'{index+1}/{len(files)} · {source.name}')
            ours=[]
            try:
                ours=launch_hidden(folder,source)
                time.sleep(.65)
                def on_one(done,total):
                    if done==total or done%max(1,total//40)==0:
                        overall=progress_base+done
                        notify(min(99,int(100*overall/total_steps)),
                               f'{index+1}/{len(files)} · {source.name} · {done}/{total} запросов')
                probes=probe_flowseal_set(folder,hosts,cancel,on_one)
                passed,latency=score(probes)
                total=len(probes)
                results.append({'strategy':source.name,'gain':None,'passed':passed,'total':total,
                    'latency_ms':latency,'probes':probes,'error':''})
            except BenchmarkError as exc:
                if cancel.is_set():raise
                results.append({'strategy':source.name,'gain':None,'passed':0,'total':steps_per_strategy,
                    'latency_ms':None,'probes':[],'error':str(exc)})
            finally:
                stop_ours(ours)
            notify(min(99,int(100*(index+1)*steps_per_strategy/total_steps)),f'{source.name} завершена')
            time.sleep(.25)
        best=choose_best(results)
        if keep_best and best and best['passed']>0 and not cancel.is_set():
            notify(99,f'Применяется {best["strategy"]}')
            try:active=launch_hidden(folder,folder/best['strategy'])
            except BenchmarkError as e:activation_error=str(e)
        if not active and original_was_running:
            # Restore only the NetPilot-managed previous strategy.
            try:
                prior=folder/Path(previous_strategy).name
                if prior.is_file():active=launch_hidden(folder,prior)
            except Exception as e:
                activation_error='Не удалось восстановить прежнюю стратегию: '+str(e)
        catalog_info={'total':len(hosts),'flowseal_targets':len(named),'sources':catalog.sources,
                      'excluded':catalog.exclusions,'skipped':list(catalog.skipped)}
        output={'results':results,'best':best,'active_pids':[p.pid for p in active],
                'activation_error':activation_error,'baseline':None,'catalog':catalog_info,
                'measures':'Flowseal-style curl -I (HTTP/1.1, TLS 1.2/1.3) plus full hostlist HTTPS. No baseline, UDP or streaming test.'}
        managed.ROOT.mkdir(parents=True,exist_ok=True)
        (managed.ROOT/'benchmark-last.json').write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
        return output
    except Exception:
        if original_was_running and not active:
            try:
                prior=folder/Path(previous_strategy).name
                if prior.is_file(): launch_hidden(folder,prior)
            except Exception:
                pass
        raise


def stop_managed_pids(pids,folder):
    if not pids:return
    expected=os.path.normcase(str((Path(folder)/'bin'/'winws.exe').resolve()))
    ours=[]
    for pid in pids:
        try:
            p=psutil.Process(pid)
            if os.path.normcase(os.path.realpath(p.exe()))==expected:ours.append(p)
        except (psutil.NoSuchProcess,psutil.AccessDenied,OSError):pass
    stop_ours(ours)
