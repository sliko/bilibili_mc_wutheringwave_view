import sys
import threading
import concurrent.futures
import random
from time import sleep
from typing import Optional
from datetime import date, datetime, timedelta

import requests
from requests.exceptions import RequestException
import os

# Provide a safe user-agent generator: use fake_useragent when available,
# otherwise fall back to a small static list to keep functionality when
# fake_useragent data files are missing in frozen builds.
STATIC_UAS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:116.0) Gecko/20100101 Firefox/116.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Safari/605.1.15',
    'Mozilla/5.0 (Linux; Android 13; Pixel 6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Mobile Safari/537.36'
]


def safe_user_agent() -> str:
    try:
        # Import UserAgent lazily to avoid fake_useragent attempting to load
        # local data files at module import time (which fails in some frozen
        # environments). If it fails for any reason, fall back to a static UA.
        from fake_useragent import UserAgent
        try:
            return UserAgent().random
        except Exception:
            return random.choice(STATIC_UAS)
    except Exception:
        return random.choice(STATIC_UAS)

# parameters
# Reduce connection timeout to 2s to speed up proxy filtering (tradeoff: may drop
# some slower-but-still-working proxies).
timeout = 2  # seconds for proxy connection timeout
thread_num = 75  # thread count for filtering active proxies
round_time = 305  # seconds for each round of view count boosting
update_pbar_count = 100  # update view count progress bar for every xx proxies
# cache filenames used by fetch_and_boost.py
CHECKERPROXY_CACHE_FILE = "checkerproxy_cache.txt"
ACTIVE_PROXIES_CACHE_FILE = "active_proxies_cache.txt"
# detect prepare-only mode before reading positional args
PREPARE_ONLY = '--prepare-proxies' in sys.argv
if not PREPARE_ONLY:
    if len(sys.argv) < 3:
        print('usage: py booster.py BV TARGET  或  py booster.py --prepare-proxies')
        sys.exit(1)
    bv = sys.argv[1]  # video BV/AV id (raw input)
    target = int(sys.argv[2])  # target view count
else:
    bv = None
    target = None

# statistics tracking parameters
successful_hits = 0  # count of successful proxy requests
initial_view_count = 0  # starting view count


def fetch_from_checkerproxy(min_count: int = 100, max_lookback_days: int = 15) -> list[str]:
    # If a cache file exists (created by fetch_and_boost), read and return it.
    try:
        if CHECKERPROXY_CACHE_FILE and os.path.exists(CHECKERPROXY_CACHE_FILE):
            with open(CHECKERPROXY_CACHE_FILE, 'r', encoding='utf-8') as cf:
                lines = [line.strip() for line in cf if line.strip()]
            if lines:
                print(f'loaded {len(lines)} proxies from cache file {CHECKERPROXY_CACHE_FILE}')
                return lines
    except Exception as e:
        print(f'error reading checkerproxy cache file: {e}')

    # Collect proxies across the requested lookback range (include today and
    # previous days), aggregate unique entries and write them to the cache file.
    collected = []
    seen = set()

    for days_back in range(0, max_lookback_days):
        day = date.today() - timedelta(days=days_back)
        proxy_url = f'https://checkerproxy.net/v1/landing/archive/{day.strftime("%Y-%m-%d")}'
        print(f'getting proxies from {proxy_url} ...')
        try:
            response = requests.get(proxy_url, timeout=timeout)
            response.raise_for_status()
        except RequestException as err:
            print(f'checkerproxy unavailable for {day.isoformat()}: {err}')
            continue

        data = response.json()
        proxies_obj = data.get('data', {}).get('proxyList', [])
        if isinstance(proxies_obj, list):
            day_proxies = proxies_obj
        elif isinstance(proxies_obj, dict):
            day_proxies = [proxy for proxy in proxies_obj.values() if proxy]
        else:
            print(f'Unexpected type of $.data.proxyList for {day}: {type(proxies_obj)}')
            day_proxies = []

        # add unique proxies preserving first-seen order
        for p in day_proxies:
            if not p:
                continue
            if p in seen:
                continue
            seen.add(p)
            collected.append(p)

        print(f'collected {len(day_proxies)} proxies from {day.isoformat()} (total collected {len(collected)})')

    if collected:
        try:
            tmp = CHECKERPROXY_CACHE_FILE + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as tf:
                for p in collected:
                    tf.write(p + '\n')
            os.replace(tmp, CHECKERPROXY_CACHE_FILE)
            print(f'cached {len(collected)} checkerproxy results to {CHECKERPROXY_CACHE_FILE}')
        except Exception as e:
            print(f'failed to write checkerproxy cache file: {e}')
        # return what we gathered (may be less than min_count)
        return collected

    return []


def fetch_from_proxyscrape() -> list[str]:
    proxy_url = ('https://api.proxyscrape.com/v2/?request=getproxies&protocol=http'
                 '&timeout=2000&country=all')
    print(f'getting proxies from {proxy_url} ...')
    response = requests.get(proxy_url, timeout=timeout + 2)
    response.raise_for_status()
    proxies = [line.strip() for line in response.text.splitlines() if line.strip()]
    print(f'successfully get {len(proxies)} proxies from proxyscrape')
    return proxies


def fetch_from_proxylistdownload() -> list[str]:
    proxy_url = 'https://www.proxy-list.download/api/v1/get?type=http'
    print(f'getting proxies from {proxy_url} ...')
    response = requests.get(proxy_url, timeout=timeout + 2)
    response.raise_for_status()
    proxies = [line.strip() for line in response.text.splitlines() if line.strip()]
    print(f'successfully get {len(proxies)} proxies from proxy-list.download')
    return proxies


def fetch_from_geonode(limit: int = 300) -> list[str]:
    proxy_url = 'https://proxylist.geonode.com/api/proxy-list'
    params = {
        'limit': limit,
        'page': 1,
        'sort_by': 'lastChecked',
        'sort_type': 'desc',
        'protocols': 'http',
    }
    print(f'getting proxies from {proxy_url} ...')
    response = requests.get(proxy_url, params=params, timeout=timeout + 2)
    response.raise_for_status()
    data = response.json().get('data', [])
    proxies = [f"{item['ip']}:{item['port']}" for item in data if item.get('ip') and item.get('port')]
    print(f'successfully get {len(proxies)} proxies from geonode')
    return proxies


def fetch_plaintext_proxy_list(url: str, label: str) -> list[str]:
    print(f'getting proxies from {url} ...')
    response = requests.get(url, timeout=max(timeout, 5))
    response.raise_for_status()
    proxies = [line.strip() for line in response.text.splitlines() if line.strip() and ':' in line]
    print(f'successfully get {len(proxies)} proxies from {label}')
    return proxies


def fetch_from_speedx() -> list[str]:
    return fetch_plaintext_proxy_list(
        'https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt',
        'TheSpeedX GitHub list')


def fetch_from_monosans() -> list[str]:
    return fetch_plaintext_proxy_list(
        'https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt',
        'monosans GitHub list')


def build_view_params(video_id: str) -> dict[str, str]:
    """Return API query params for either BV or AV id."""
    normalized = video_id.strip()
    if not normalized:
        raise ValueError('video id is empty')
    lowered = normalized.lower()
    if lowered.startswith('av'):
        aid = normalized[2:]
        if not aid.isdigit():
            raise ValueError(f'invalid av id: {video_id}')
        return {'aid': aid}
    if normalized.isdigit():
        return {'aid': normalized}
    return {'bvid': normalized}


def fetch_video_info(video_id: str) -> dict:
    """Fetch video metadata and ensure API response is valid."""
    params = build_view_params(video_id)
    response = requests.get(
        'https://api.bilibili.com/x/web-interface/view',
        params=params,
        headers={'User-Agent': safe_user_agent()},
        timeout=timeout + 2
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get('code') != 0 or 'data' not in payload:
        msg = payload.get('message', 'unknown error')
        raise RuntimeError(f'bilibili API error: code={payload.get("code")} message={msg}')
    data = payload['data']
    if not data.get('aid') or not data.get('bvid'):
        raise RuntimeError('video info missing key identifiers')
    return data


def get_total_proxies() -> list[str]:
    fetchers = [
        ('checkerproxy', fetch_from_checkerproxy),
        ('proxyscrape', fetch_from_proxyscrape),
        ('proxy-list.download', fetch_from_proxylistdownload),
        ('geonode', fetch_from_geonode),
        ('speedx', fetch_from_speedx),
        ('monosans', fetch_from_monosans),
    ]
    all_proxies: set[str] = set()
    for name, fetcher in fetchers:
        try:
            proxies = fetcher()
        except RequestException as err:
            print(f'{name} source failed: {err}')
            continue
        except Exception as err:
            print(f'{name} source error: {err}')
            continue
        for proxy in proxies:
            all_proxies.add(proxy)
        if len(all_proxies) >= 500:
            break
    if all_proxies:
        print(f'collected {len(all_proxies)} proxies from available sources')
        return list(all_proxies)
    raise RuntimeError('failed to fetch proxies from all sources')


def time(seconds: int) -> str:
    if seconds < 60:
        return f'{seconds}s'
    else:
        return f'{int(seconds / 60)}min {seconds % 60}s'

def pbar(n: int, total: int, hits: Optional[int], view_increase: Optional[int]) -> str:
    # 不再输出条状进度，仅显示数字和可选统计信息
    if hits is None or view_increase is None:
        return f'\r{n}/{total}'
    else:
        return f'\r{n}/{total} [Hits: {hits}, Views+: {view_increase}]'

# 1.get proxy
print()
total_proxies = get_total_proxies()

# 2.filter proxies by multi-threading
if len(total_proxies) > 100000:
    print('more than 100000 proxies, randomly pick 100000 proxies')
    random.shuffle(total_proxies)
    total_proxies = total_proxies[:100000]
active_proxies = []

# If active proxies cache exists (prepared by fetch_and_boost), load and skip filtering
if os.path.exists(ACTIVE_PROXIES_CACHE_FILE):
    with open(ACTIVE_PROXIES_CACHE_FILE, 'r', encoding='utf-8') as af:
        lines = [line.strip() for line in af if line.strip()]
    if lines:
        active_proxies = lines
        print(f'loaded {len(active_proxies)} active proxies from cache {ACTIVE_PROXIES_CACHE_FILE}')
    else:
        print(f'active proxies cache {ACTIVE_PROXIES_CACHE_FILE} is empty, will filter')
else:
    print('\nfiltering active proxies using http://httpbin.org/post ...')
    start_filter_time = datetime.now()

    # Use a thread pool to check proxies concurrently. Use a thread-local
    # requests.Session so connections can be reused per worker thread which
    # significantly speeds up bulk checking compared to creating new sessions
    # for every request.
    thread_local = threading.local()

    def check_proxy(proxy: str) -> Optional[str]:
        try:
            session = getattr(thread_local, 'session', None)
            if session is None:
                session = requests.Session()
                thread_local.session = session
            # include both http and https in proxies mapping
            session.post('http://httpbin.org/post', proxies={'http': 'http://'+proxy, 'https': 'http://'+proxy}, timeout=timeout)
            return proxy
        except Exception:
            return None

    max_workers = min(200, len(total_proxies)) or 1
    active_set = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as exe:
        futures = {exe.submit(check_proxy, proxy): proxy for proxy in total_proxies}
        for fut in concurrent.futures.as_completed(futures):
            res = fut.result()
            if res:
                active_set.append(res)

    # preserve order and deduplicate
    seen = set()
    for p in active_set:
        if p in seen:
            continue
        seen.add(p)
        active_proxies.append(p)

    filter_cost_seconds = int((datetime.now() - start_filter_time).total_seconds())
    print(f'\nsuccessfully filter {len(active_proxies)} active proxies in {filter_cost_seconds} seconds')

    # write active proxies cache for other processes
    tmp = ACTIVE_PROXIES_CACHE_FILE + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8') as tf:
            for p in active_proxies:
                tf.write(p + '\n')
        os.replace(tmp, ACTIVE_PROXIES_CACHE_FILE)
        print(f'cached {len(active_proxies)} active proxies to {ACTIVE_PROXIES_CACHE_FILE}')
    except Exception as e:
        print(f'failed to write active proxies cache file: {e}')

# If running in prepare-only mode, exit after ensuring active_proxies cache exists
if PREPARE_ONLY:
    print(f'prepare-only mode complete: {len(active_proxies)} active proxies ready')
    sys.exit(0)

# 3.boost view count
print(f'\nstart boosting {bv} at {datetime.now().strftime("%H:%M:%S")}')
current = 0
info = {}  # Initialize info dictionary

# Get initial view count
try:
    info = fetch_video_info(bv)
    bv = info['bvid']  # ensure BV id is normalized for later requests
    initial_view_count = info['stat']['view']
    current = initial_view_count
    target = initial_view_count + target  # adjust target to absolute view count
    print(f'Initial view count: {initial_view_count}')
except Exception as e:
    print(f'Failed to get initial view count: {e}')
    sys.exit(1)

while True:
    reach_target = False
    start_time = datetime.now()
    end_num=0
    
    # send POST click request for each proxy
    for i, proxy in enumerate(active_proxies):
        try:
            if i % update_pbar_count == 0:  # update progress bar
                print(f'{pbar(current, target, successful_hits, current - initial_view_count)} updating view count...', end='')
                info = fetch_video_info(bv)
                current = info['stat']['view']
                biliuserid = info['owner']['mid']
                if biliuserid is not None and int(biliuserid) == 5546092:
                    print(f'"{biliuserid}跳过视频，因为x',end='' )
                    reach_target = True
                    break
                if current >= target:
                    reach_target = True
                    print(f'{pbar(current, target, successful_hits, current - initial_view_count)} done                 ', end='')
                    break
               

                requests.post('http://api.bilibili.com/x/click-interface/click/web/h5',
                          proxies={'http': 'http://'+proxy},
                          headers={'User-Agent': safe_user_agent()},
                          timeout=timeout,
                          data={
                              'aid': info['aid'],
                              'cid': info['cid'],
                              'bvid': bv,
                              'part': '1',
                              'mid': info['owner']['mid'],
                              'jsonp': 'jsonp',
                              'type': info['desc_v2'][0]['type'] if info['desc_v2'] else '1',
                              'sub_type': '0'
                          })
            successful_hits += 1
            if successful_hits/len(active_proxies) > 3:
                    print(f'{pbar(current, target, successful_hits, current - initial_view_count)} 尝试3次，跳过                 ', end='')
                    reach_target = True
                    break
            if successful_hits % update_pbar_count == 0:
                print(f'{pbar(current, target, successful_hits, current - initial_view_count)} proxy({i+1}/{len(active_proxies)}) success   ', end='')
        except:  # proxy connect timeout
            if successful_hits % update_pbar_count == 0:
                print(f'{pbar(current, target, successful_hits, current - initial_view_count)} proxy({i+1}/{len(active_proxies)}) fail      ', end='')
        
    if reach_target:  # reach target view count
        break
    remain_seconds = int(round_time-(datetime.now()-start_time).total_seconds())
    if remain_seconds > 0:
        # for second in reversed(range(remain_seconds)):
        #    print(f'{pbar(current, target, successful_hits, current - initial_view_count)} next round: {time(second)}          ', end='')
        sleep(1)

success_rate = (successful_hits / len(active_proxies)) * 100 if active_proxies else 0
print(f'\nFinish at {datetime.now().strftime("%H:%M:%S")}')
print(f'Statistics:')
print(f'- Initial views: {initial_view_count}')
print(f'- Final views: {current}')
print(f'- Total increase: {current - initial_view_count}')
print(f'- Successful hits: {successful_hits}')
print(f'- Success rate: {success_rate:.2f}%\n')
