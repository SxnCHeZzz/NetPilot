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


def probe_host(host):
    try:
        h,status,ms=managed.ping_web(host,timeout=PER_HOST_TIMEOUT)
        return {'host':h,'ok':200<=status<600,'ms':ms,'status':status}
    except Exception as e:
        return {'host':host,'ok':False,'ms':None,'error':str(e)[:120]}


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
        executor.shutdown(wait=False,cancel_futures=True)


def benchmark(folder,notify,cancel,*,keep_best=False):
    folder=Path(folder).resolve()
    files=[Path(p) for p in preflight(folder)]
    catalog=load_catalog(folder);hosts=catalog.hosts
    results=[];total_steps=len(files)*len(hosts)
    try:
        notify(0, f'Проверка {len(files)} стратегий · {len(hosts)} адресов')
        for index, source in enumerate(files):
            if cancel.is_set():raise BenchmarkError('Тест отменён.')
            progress_base=index*len(hosts)
            notify(int(100*progress_base/total_steps),f'{index+1}/{len(files)} · {source.name}: запуск | {len(hosts)} адресов')
            ours=[]
            try:
                ours=launch_hidden(folder,source)
                time.sleep(.65)
                def on_one(done,total):
                    if done==total or done%max(1,total//25)==0:
                        overall=progress_base+done
                        notify(min(99,int(100*overall/total_steps)),
                               f'Стратегия {index+1}/{len(files)} · {source.name} · адреса {done}/{total}')
                probes=scan_hosts(hosts,cancel,on_one)
                passed,latency=score(probes)
                results.append({'strategy':source.name,'gain':None,'passed':passed,'total':len(hosts),
                    'latency_ms':latency,'probes':probes,'error':''})
            except BenchmarkError as e:
                if cancel.is_set():raise
                results.append({'strategy':source.name,'gain':None,'passed':0,'total':len(hosts),
                    'latency_ms':None,'probes':[],'error':str(e)})
            finally:
                stop_ours(ours)
            notify(int(100*(index+1)*len(hosts)/total_steps),f'{source.name} · проверка завершена')
            time.sleep(.35)
        best=choose_best(results)
        active=[];activation_error=''
        if keep_best and best and best.get('passed',0)>0 and not cancel.is_set():
            notify(99,f'Подключение стратегии: {best["strategy"]}')
            try:active=launch_hidden(folder,folder/best['strategy'])
            except BenchmarkError as e:activation_error=str(e)
        result={'results':results,'best':best,'active_pids':[p.pid for p in active],
                'activation_error':activation_error,
                'baseline':None,  # Deliberately no preliminary probe without Zapret.
                'catalog':{'total':len(hosts),'sources':catalog.sources,
                           'excluded':catalog.exclusions,'skipped':list(catalog.skipped)},
                'measures':'HTTPS only; no baseline scan. Higher reachability is a heuristic, not a guarantee for voice/video/UDP.'}
        managed.ROOT.mkdir(parents=True,exist_ok=True)
        (managed.ROOT/'benchmark-last.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        return result
    finally:
        # No service mutation and no changes to the original upstream BAT scripts.
        pass


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
