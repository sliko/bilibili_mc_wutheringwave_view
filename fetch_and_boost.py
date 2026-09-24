#!/usr/bin/env python3
import requests
import sys
import time
import datetime
import argparse
import concurrent.futures
import subprocess
import os
import threading

LOG_FILE = "log.txt"
DEFAULT_KEYWORD = "鸣潮创作激励计划"
# 请求间隔（秒）和重试等待时间（秒）
REQUEST_INTERVAL = 1
RETRY_WAIT = 1

lock = threading.Lock()

def log(msg):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}\n"
    with lock:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line)
    print(line, end="")

def ensure_root_get(session):
    try:
        session.get("https://bilibili.com", timeout=10)
        log("已对 https://bilibili.com 进行初始 GET，获取 cookies")
    except Exception as e:
        log(f"初始 GET 失败: {e}")

def load_active_proxies():
    PROXY_FILE = 'active_proxies_cache.txt'
    if not os.path.exists(PROXY_FILE):
        return []
    try:
        with open(PROXY_FILE, 'r', encoding='utf-8') as pf:
            lines = [line.strip() for line in pf if line.strip()]
        return lines
    except Exception:
        return []


def run_prepare_proxies(booster_cmd):
    try:
        if isinstance(booster_cmd, str):
            cmd = booster_cmd.strip().split()
        else:
            cmd = list(booster_cmd)
        cmd = cmd + ['--prepare-proxies']
        log(f"调用 booster 进行预过滤: {' '.join(cmd)}")
        prep_proc = subprocess.run(cmd, capture_output=True, text=True)
        log(f"booster 预过滤返回码={prep_proc.returncode}")
        if prep_proc.stdout:
            log(f"booster 预过滤 stdout:\n{prep_proc.stdout.strip()}")
        if prep_proc.stderr:
            log(f"booster 预过滤 stderr:\n{prep_proc.stderr.strip()}")
    except Exception as e:
        log(f"执行 booster 预过滤出错: {e}")


def search_bv_list(session, keyword, days=3, max_pages=50, booster_cmd=None):
    """
    Fetch up to max_pages pages from the search API. For each page, retry until success.
    Only include videos whose pubdate is within `days` from now.
    """
    # If a local bv.txt exists, use it directly and skip online searching.
    BV_FILE = 'bv.txt'
    if os.path.exists(BV_FILE):
        try:
            with open(BV_FILE, 'r', encoding='utf-8') as bf:
                lines = [line.strip() for line in bf if line.strip()]
            if lines:
                log(f"检测到本地 {BV_FILE}，直接读取 {len(lines)} 个 BV 并跳过网络搜索")
                # return list of tuples (pub, bvid) with pub=0 since we don't have timestamps
                return [(0, bvid) for bvid in lines]
        except Exception as e:
            log(f"读取 {BV_FILE} 失败，继续在线搜索: {e}")

    threshold = int(time.time()) - days * 86400
    bvs = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Referer": "https://www.bilibili.com/",
        "Accept": "application/json, text/javascript, */*; q=0.01"
    }

    # proxies list loaded from active_proxies_cache.txt when available
    proxies_list = []
    proxy_idx = 0

    for pn in range(1, max_pages + 1):
        params = {
            "search_type": "video",
            "keyword": keyword,
            "page": pn,
            "order": "pubdate"
        }
        data = None
        attempt = 0
        # 重试直到成功（按照用户要求）
        while True:
            attempt += 1
            try:
                req_kwargs = dict(
                    params=params,
                    headers=headers,
                    timeout=10,
                    cookies=session.cookies,
                )
                # if we have proxies loaded, use current proxy
                if proxies_list:
                    proxy = proxies_list[proxy_idx % len(proxies_list)]
                    req_kwargs['proxies'] = {'http': 'http://' + proxy, 'https': 'http://' + proxy}
                resp = session.get("https://api.bilibili.com/x/web-interface/search/type", **req_kwargs)
            except Exception as e:
                log(f"搜索第 {pn} 页请求失败 (第{attempt}次): {e}")
                # if too many failures on this page, try refreshing proxies
                if attempt > 12:
                    log(f"搜索第 {pn} 页连续失败超过12次，尝试刷新代理并切换代理")
                    # first, try to generate checkerproxy_cache.txt and active_proxies_cache.txt
                    if booster_cmd:
                        run_prepare_proxies(booster_cmd)
                        # reload proxies
                        proxies_list = load_active_proxies()
                        proxy_idx = 0
                        log(f"加载到 {len(proxies_list)} 个活动代理，切换重试")
                    else:
                        log("未提供 booster_cmd，无法刷新代理")
                    attempt = 0
                    time.sleep(RETRY_WAIT)
                    continue
                time.sleep(RETRY_WAIT)
                # if using proxies, advance to next proxy on repeated failures
                if proxies_list and attempt % 3 == 0:
                    proxy_idx += 1
                    log(f"切换到下一个代理 index={proxy_idx}")
                continue

            try:
                data = resp.json()
            except Exception:
                snippet = resp.text[:1000].replace('\n', ' ')
                log(f"搜索第 {pn} 页返回非 JSON 内容 (第{attempt}次): {snippet}，{RETRY_WAIT}秒后重试")
                time.sleep(RETRY_WAIT)
                continue

            if data.get("code") != 0:
                log(f"搜索第 {pn} 页接口返回错误 code={data.get('code')} message={data.get('message')} (第{attempt}次)，{RETRY_WAIT}秒后重试")
                time.sleep(RETRY_WAIT)
                continue

            # 成功获取数据，跳出重试循环
            break

        results = data.get("data", {}).get("result", [])
        if not results:
            log(f"搜索第 {pn} 页无结果，继续下一页")
            time.sleep(REQUEST_INTERVAL)
            continue

        for item in results:
            pub = item.get("pubdate") or item.get("created") or 0
            bvid = item.get("bvid") or None
            arc = item.get("arc") or {}
            if not bvid:
                bvid = arc.get("bvid") or item.get("bvid")
            if not bvid:
                continue
            # 如果上传者 mid 为 x 则跳过该条记录
            mid = None
            try:
                mid = item.get('mid') or item.get('owner', {}).get('mid') or (arc.get('owner') or {}).get('mid')
            except Exception:
                mid = None
            try:
                if mid is not None and int(mid) == 5546092:
                    log(f"跳过视频，因为x")
                    continue
            except Exception:
                # 无法解析 mid，忽略并继续处理
                pass
            # 仅保留最近 days 天的视频
            if pub < threshold:
                continue
            bvs.append((pub, bvid))
        # 小间隔以免请求过快
        time.sleep(REQUEST_INTERVAL)

    # keep only unique bvids, sorted by pub ascending (时间顺序)
    seen = set()
    ordered = []
    for pub, bvid in sorted(bvs, key=lambda x: x[0]):
        if bvid in seen:
            continue
        seen.add(bvid)
        ordered.append((pub, bvid))
    return ordered

def run_booster(bvid, value, booster_cmd):
    cmd = booster_cmd + [bvid, str(value)]
    log(f"启动 booster: {' '.join(cmd)}")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
        out = proc.stdout or ""
        err = proc.stderr or ""
        ret = proc.returncode
        log(f"booster 结束 BV={bvid} 返回码={ret}")
        if out:
            log(f"booster stdout BV={bvid}:\n{out.strip()}")
        if err:
            log(f"booster stderr BV={bvid}:\n{err.strip()}")
    except Exception as e:
        log(f"执行 booster 出错 BV={bvid}: {e}")

def main():
    parser = argparse.ArgumentParser(description="在 B 站搜索并并行调用 booster.py")
    parser.add_argument("--keyword", "-k", default=DEFAULT_KEYWORD, help="搜索关键词")
    parser.add_argument("--days", "-d", type=int, default=3, help="最近 N 天 (默认3)")
    parser.add_argument("--concurrency", "-c", type=int, default=4, help="并行任务数 (默认4)")
    parser.add_argument("--value", "-v", type=int, default=248, help="传给 booster 的数值 (默认248)")
    # When bundled into a single-file exe, default to invoking the same exe with --run-booster
    if getattr(sys, 'frozen', False):
        default_booster = [sys.executable, '--run-booster']
    else:
        default_booster = "py booster.py"
    parser.add_argument("--booster", "-b", default=default_booster, help="启动 booster 的命令（默认: py booster.py，打包时为 exe --run-booster）")
    parser.add_argument("--max-pages", type=int, default=50, help="搜索最大页数")
    args = parser.parse_args()

    session = requests.Session()
    session.headers.update({"User-Agent":"Mozilla/5.0"})
    ensure_root_get(session)

    log(f"开始搜索 关键词='{args.keyword}' 最近 {args.days} N天的视频")
    booster_cmd = args.booster
    found = search_bv_list(session, args.keyword, days=args.days, max_pages=args.max_pages, booster_cmd=booster_cmd)
    if not found:
        log("未找到符合条件的视频。")
        return

    # 将 BV 列表单独写入 bv.txt（每行一个 BV，按时间顺序从旧到新）
    BV_FILE = "bv.txt"
    with open(BV_FILE, "w", encoding="utf-8") as bf:
        for pub, bvid in found:
            bf.write(f"{bvid}\n")
    log(f"找到的视频 BV 列表已写入 {BV_FILE} (按时间顺序，从旧到新)")

    if isinstance(args.booster, str):
        booster_cmd = args.booster.strip().split()
    else:
        booster_cmd = args.booster

    # 运行 booster 的预取模式，令其读取 checkerproxy_cache.txt 并生成 active_proxies_cache.txt
    try:
        if isinstance(args.booster, str):
            prep_cmd = args.booster.strip().split()
        else:
            prep_cmd = args.booster
        prep_cmd = prep_cmd + ['--prepare-proxies']
        log(f"调用 booster 进行预过滤: {' '.join(prep_cmd)}")
        prep_proc = subprocess.run(prep_cmd, capture_output=True, text=True)
        log(f"booster 预过滤返回码={prep_proc.returncode}")
        if prep_proc.stdout:
            log(f"booster 预过滤 stdout:\n{prep_proc.stdout.strip()}")
        if prep_proc.stderr:
            log(f"booster 预过滤 stderr:\n{prep_proc.stderr.strip()}")
    except Exception as e:
        log(f"执行 booster 预过滤出错: {e}")

    bvids = [bvid for _, bvid in found]
    log(f"开始并行启动 booster，总数={len(bvids)} 并发={args.concurrency} value={args.value}")
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as exe:
        futures = []
        for bvid in bvids:
            futures.append(exe.submit(run_booster, bvid, args.value, booster_cmd))
        for fut in concurrent.futures.as_completed(futures):
            pass

    log("所有 booster 任务已完成。")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log('收到中断信号，退出')
        try:
            # allow graceful exit
            sys.exit(0)
        except SystemExit:
            pass
