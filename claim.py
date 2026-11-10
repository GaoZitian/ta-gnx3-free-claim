#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ta.gnx3.cn 免费福利区自动领取 + 每日签到
================================================
- 登录：Playwright 真实浏览器过拼图滑块（NCC 定位 + 拟人拖动）
- 签到：user/ajax_user.php?act=qiandao
- 领取：ajax_shop.php?act=captcha（服务端直接返回缺口 x）+ act=pay 提交
- 互斥：每天全局只成功领取一个商品（本地状态 + 服务端互斥双保险）
- 持久化：cookie 缓存（mysid 约 7 天有效），GitHub Actions 用 actions/cache
- 通知：Telegram Bot
"""
import os
import re
import sys
import io
import json
import time
import random
import base64
import subprocess
from datetime import datetime

import requests
import numpy as np
from PIL import Image

try:
    import yaml
except ImportError:
    yaml = None

try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_OK = True
except ImportError:
    PLAYWRIGHT_OK = False

# ============================================================
# 常量
# ============================================================
BASE = 'https://ta.gnx3.cn'
# Playwright 用桌面 UA（登录页在桌面版有完整表单 + 滑块；纯 requests 用手机 UA 无影响）
PW_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
         '(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36')
UA = ('Mozilla/5.0 (iPhone; CPU iPhone OS 16_3_0 like Mac OS X) '
      'AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/134.0.6998.99 '
      'Mobile/15E148 Safari/604.1')

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(SCRIPT_DIR, '.cache')
COOKIE_FILE = os.path.join(CACHE_DIR, 'cookies.json')
STATE_FILE = os.path.join(CACHE_DIR, 'state.json')
CONFIG_FILE = os.path.join(SCRIPT_DIR, 'config.yaml')

# 站点访问头：桌面 UA 会被入口脚本跳到 /chat.html 提示页，必须用手机 UA
# 且 navigator.platform 系站点前端逻辑，纯 requests 不执行 JS，无影响

# ============================================================
# 内置商品清单（免费福利区，13 个，全部互斥：每天全局只能成功领一个）
# 注意：站点免费福利区还有其他免费商品（优惠券类等），本脚本不做考虑。
# amount = 每日默认数量（来自站点商品页）；input_type 决定领取时要填什么
# implemented=False 表示暂不实现（QQ 说说浏览量需通过 QQ 获取说说 id，难度高）
# 当前 13 个中：12 个可正常领取，1 个（5186 说说浏览量）暂不实现。
# ============================================================
GOODS = {
    'douyin': [
        {'tid': 5564, 'name': '作品点赞10-1万',   'amount': 10,   'input_type': 'link'},
        {'tid': 5539, 'name': '作品收藏10-1万',   'amount': 10,   'input_type': 'link'},
        {'tid': 5540, 'name': '作品分享100-1万',  'amount': 100,  'input_type': 'link'},
        {'tid': 3140, 'name': '作品播放1千-10万', 'amount': 1000, 'input_type': 'link'},
    ],
    'kuaishou': [
        {'tid': 2170, 'name': '作品点赞10-1万',   'amount': 10,   'input_type': 'link'},
        {'tid': 5570, 'name': '福利粉丝10-1万',   'amount': 10,   'input_type': 'link'},
        {'tid': 5535, 'name': '作品收藏10-1万',   'amount': 10,   'input_type': 'link'},
        {'tid': 5536, 'name': '作品播放1百-1万',  'amount': 100,  'input_type': 'link'},
        {'tid': 5537, 'name': '作品分享1百-1万',  'amount': 100,  'input_type': 'link'},
    ],
    'qq': [
        {'tid': 5700, 'name': '豪华代挂等级加速1天',  'amount': 0, 'input_type': 'qq'},
        {'tid': 5542, 'name': '名片赞1百-10万随机',   'amount': 1, 'input_type': 'qq'},
        {'tid': 5379, 'name': '空间访客1百-10万随机', 'amount': 1, 'input_type': 'qq'},
        {'tid': 5186, 'name': '说说浏览量1百-100万随机', 'amount': 1, 'input_type': 'qq', 'implemented': False},
    ],
}

INPUT_TYPE_LABEL = {'link': '作品链接', 'qq': 'QQ号码'}
ENV_INPUT = {'douyin': 'TA_DOUYIN_INPUT', 'kuaishou': 'TA_KUAISHOU_INPUT', 'qq': 'TA_QQ_INPUT'}

# 服务端"已领过/互斥"类文案（实测后可补充）
MUTEX_PAT = re.compile(r'已领|领取过|明日|明天|每天|今日已|上限|次数|再来')

MAX_SLIDE_RETRY = 3              # 滑块最大重试（Playwright 命中率 ~100%，3 次足够）
VERIFY_DURATION = (1500, 2500)  # verify duration 毫秒区间，<600 必挂


def now():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def log(*a):
    print(f'[{now()}]', *a, flush=True)


# ============================================================
# Playwright 滑块（真实浏览器 + NCC 定位 + 拟人拖动）
# 纯 requests 方案实测命中率仅 ~12%（服务端对 x 容差极小 + 背景纯暗定位不可靠），
# 改用 Playwright 真实鼠标拖动后实测命中率 ~100%（6/6）。这是根治方案。
# ============================================================
def _ncc_x(bg_b64, pc_b64, y_px):
    """NCC 模板匹配：piece 图案 = 缺口处背景图案。返回缺口左边缘 x（0..264）。"""
    try:
        bg = Image.open(io.BytesIO(base64.b64decode(bg_b64.split(',')[1]))).convert('RGBA')
        pc = Image.open(io.BytesIO(base64.b64decode(pc_b64.split(',')[1]))).convert('RGBA')
    except Exception:
        return -1
    B = np.array(bg.convert('RGB')).astype(np.float64)
    P = np.array(pc.convert('RGBA')).astype(np.float64)
    H, W = B.shape[:2]
    PH, PW = P.shape[:2]
    m = P[:, :, 3] > 200
    if m.sum() < 100:
        return -1
    Pg = P[:, :, :3].mean(axis=2)
    pm = Pg[m]
    pm_c = pm - pm.mean()
    pm_norm = np.sqrt((pm_c ** 2).sum())
    if pm_norm < 1e-6:
        return -1
    Bg = B.mean(axis=2)
    best_score, best_x = -1e9, -1
    ys, ye = y_px, min(H, y_px + PH)
    for x in range(0, W - PW + 1):
        sub = Bg[ys:ye, x:x + PW]
        if sub.shape[0] < PH:
            pad = np.zeros((PH, PW))
            pad[:sub.shape[0], :] = sub
            sub = pad
        sm = sub[m]
        sm_c = sm - sm.mean()
        denom = np.sqrt((sm_c ** 2).sum()) * pm_norm
        if denom < 1e-6:
            continue
        score = (sm_c * pm_c).sum() / denom
        if score > best_score:
            best_score, best_x = score, x
    return best_x


def _drag_human(page, handle, track, x_target):
    """拟人化拖动滑块到 x_target（0..264 拼图位移）：ease-out 缓动 + 微抖 + 随机步数。"""
    hb = handle.bounding_box()
    tb = track.bounding_box()
    travel = tb['width'] - hb['width']
    px_dist = x_target / 264 * travel
    start_x = hb['x'] + hb['width'] / 2
    start_y = hb['y'] + hb['height'] / 2
    end_x = start_x + px_dist
    page.mouse.move(start_x, start_y)
    page.mouse.down()
    steps = random.randint(28, 42)
    for s in range(1, steps + 1):
        t = s / steps
        eased = 1 - (1 - t) ** 2.2  # 先快后慢
        cx = start_x + (end_x - start_x) * eased
        if random.random() < 0.3:
            cx += random.uniform(-1.2, 1.2)
        page.mouse.move(cx, start_y)
        time.sleep(random.uniform(0.012, 0.022))
    page.mouse.up()


def pw_login(user, pwd):
    """用 Playwright 真实浏览器登录。

    成功返回 (cookie_dict, login_response, False)；失败返回 (None, None, False)。
    - 每次尝试都用全新浏览器 + 全新 context（全新会话），避免同会话二次
      GET login.php 产生 mysid 污染导致登录页消失（GitHub Actions 数据中心 IP 实测触发）
    - Playwright 打开 PC 登录页 → 填账号密码 → 点登录触发滑块
    - NCC 定位缺口 → 拟人拖动 → 滑块通过 → 浏览器自动提交 ajax.php?act=login
    - 登录成功后导出全部浏览器 cookie（供 requests session 继续签到/领取）
    """
    if not PLAYWRIGHT_OK:
        log('✗ 未安装 playwright（pip install playwright && playwright install chromium）')
        return None, None, False

    with sync_playwright() as p:
        login_result = {}
        login_ok = False
        last_cookies = None

        for attempt in range(1, MAX_SLIDE_RETRY + 1):
            # 每次尝试：全新浏览器 + 全新 context（全新会话，无 mysid 残留）
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(
                user_agent=PW_UA,
                viewport={'width': 1280, 'height': 900})
            page = ctx.new_page()

            try:
                page.goto(BASE + '/user/login.php', wait_until='domcontentloaded', timeout=30000)
                time.sleep(2)

                # C：防御检查——input 不存在则本次尝试作废，重启浏览器重试
                if page.locator('input[name=user]').count() == 0:
                    log(f'  [滑块 {attempt}/{MAX_SLIDE_RETRY}] 登录页无输入框，重启浏览器重试')
                    browser.close()
                    time.sleep(1.5)
                    continue

                page.fill('input[name=user]', user)
                page.fill('input[name=pass]', pwd)

                login_result = {}

                def on_response(resp):
                    url = resp.url
                    # 诊断：记录所有 ajax.php 和 ds_slide 响应（不读 body，避免 CI 竞态）
                    if 'ajax.php' in url or 'ds_slide/api.php' in url:
                        log(f'    [RESP] {url} status={resp.status}')
                page.on('response', on_response)

                page.click('input[type=submit]')
                time.sleep(2.5)
                handle = page.locator('.ds-slide-handle')
                if handle.count() == 0:
                    log(f'  [滑块 {attempt}/{MAX_SLIDE_RETRY}] 滑块未弹出')
                    browser.close()
                    time.sleep(1.5)
                    continue

                bg_src = page.evaluate("() => document.querySelector('.ds-slide-background')?.src")
                pc_src = page.evaluate("() => document.querySelector('.ds-slide-piece')?.src")
                top_pct = page.evaluate("() => parseFloat(document.querySelector('.ds-slide-piece').style.top)")
                y_px = int(float(top_pct) / 100 * 160)
                x = _ncc_x(bg_src, pc_src, y_px)
                if x < 0:
                    log(f'  [滑块 {attempt}/{MAX_SLIDE_RETRY}] NCC 定位失败')
                    browser.close()
                    time.sleep(1.5)
                    continue

                track = page.locator('.ds-slide-track')
                # 拖动前启动 login 响应等待（expect_response 拿到 response 后 finished() 等 body 下载完再读，
                # 避免 on_response 回调里同步 text()/json() 在 GitHub Actions 上 body 读取竞态失败）
                resp_info = None
                try:
                    with page.expect_response(
                            lambda r: 'ajax.php?act=login' in r.url,
                            timeout=15000) as ri:
                        _drag_human(page, handle, track, x)
                        resp_info = ri
                except Exception:
                    log(f'  [滑块 {attempt}/{MAX_SLIDE_RETRY}] 等待 login 响应超时/失败')
                    resp_info = None

                if resp_info is not None:
                    try:
                        # 直接 json()（内部会等 body 完整下载），避免 finished() 在页面关闭时抛 Target closed
                        login_result.update(resp_info.value.json())
                    except Exception as e:
                        log(f'    [WARN] 读取 login 响应失败: {e}')
                time.sleep(2.5)

                if login_result.get('code') == 0:
                    login_ok = True
                    last_cookies = {c['name']: c['value'] for c in ctx.cookies() if c.get('value')}
                    browser.close()
                    break

                dialog_gone = page.evaluate("() => !document.querySelector('.ds-slide-dialog')")
                if login_result:
                    log(f'  [滑块 {attempt}/{MAX_SLIDE_RETRY}] 登录响应: '
                        f'{login_result.get("code")} {login_result.get("msg", "")[:40]}')
                elif dialog_gone:
                    log(f'  [滑块 {attempt}/{MAX_SLIDE_RETRY}] 面板关闭但无登录响应，重试')
                else:
                    log(f'  [滑块 {attempt}/{MAX_SLIDE_RETRY}] 验证失败，换一张')
            except Exception as e:
                log(f'  [滑块 {attempt}/{MAX_SLIDE_RETRY}] 异常: {e}（重启浏览器重试）')
            finally:
                try:
                    browser.close()
                except Exception:
                    pass
            time.sleep(1.5)

        if not login_result:
            return None, None, False

        if not login_ok:
            log(f'  ✗ 登录失败: {login_result.get("msg", login_result)}')
            return None, login_result, False
        log(f'  ✓ 登录成功（cookie {len(last_cookies or {})} 个）')
        return last_cookies, login_result, False


# ============================================================
# node 求值（JSFuck / sec_defend / hashsalt）
# ============================================================
def node_eval(expr):
    """用 node 执行一个 JS 表达式（站点反爬用 JSFuck 混淆，必须真实求值）"""
    try:
        r = subprocess.run(['node', '-e', 'console.log((' + expr + '))'],
                           capture_output=True, text=True, timeout=15)
        return r.stdout.strip()
    except Exception as e:
        log('  node 执行失败:', e)
        return ''


# ============================================================
# sec_defend 反爬（关键：只 GET /user/login.php 一次）
# 访问首页 / 或二次 GET 都会产生 mysid cookie 污染会话，
# 导致后续滑块 verify 一律 4601（实测确认）
# ============================================================
# ============================================================
# 会话
# ============================================================
def build_session(cookie_dict=None):
    s = requests.Session()
    s.headers.update({
        'User-Agent': UA,
        'Accept-Language': 'zh-CN,zh-Hans;q=0.9',
        'Origin': BASE,
    })
    if cookie_dict:
        for k, v in cookie_dict.items():
            s.cookies.set(k, v, domain='ta.gnx3.cn')
    return s


def cookie_dict_of(sess):
    return {c.name: c.value for c in sess.cookies}


# ============================================================
# 接口封装
# ============================================================
def login(sess, user, pwd, ticket):
    r = sess.post(BASE + '/ajax_shop.php?act=login',
                  data={'user': user, 'pass': pwd, 'ds_slide_ticket': ticket},
                  headers={
                      'Content-Type': 'application/x-www-form-urlencoded',
                      'X-Requested-With': 'XMLHttpRequest',
                      'Accept': 'application/json, text/javascript, */*; q=0.01',
                      'Referer': BASE + '/?chadan=1',
                  })
    try:
        return r.json()
    except Exception:
        return {'code': -1, 'msg': r.text[:150]}


def qiandao(sess):
    r = sess.get(BASE + '/user/ajax_user.php?act=qiandao', headers={
        'Accept': 'application/json, text/javascript, */*; q=0.01',
        'X-Requested-With': 'XMLHttpRequest',
        'Referer': BASE + '/user/qiandao.php',
    })
    try:
        return r.json()
    except Exception:
        return {'code': -1, 'msg': r.text[:150]}


def warmup_after_login(sess):
    """登录接口会重置会话，必须先走一遍登录页/用户页再签到，否则签到返回校验失败"""
    try:
        sess.get(BASE + '/user/login.php')
        sess.get(BASE + '/user/', headers={'Accept': 'text/html,application/xhtml+xml'})
        sess.get(BASE + '/user/qiandao.php', headers={'Accept': 'text/html,application/xhtml+xml'})
    except Exception as e:
        log('  预热页面失败(忽略):', e)


def profile(sess):
    """取余额 / 今日收益"""
    try:
        r = sess.get(BASE + '/user/', headers={'Accept': 'text/html,application/xhtml+xml'})
        h = r.text

        def find(pat):
            m = re.search(pat, h)
            return m.group(1) if m else None
        return {
            'balance': find(r'余额[：:]\s*(?:<[^>]*>\s*)?([0-9.]+)'),
            'today': find(r'今日收益[\s\S]{0,80}?([0-9.]+)\s*元'),
        }
    except Exception:
        return {}


def get_hashsalt(sess):
    r = sess.get(BASE + '/ajax_shop.php?act=hashsalt', headers={
        'Accept': 'application/json',
        'X-Requested-With': 'XMLHttpRequest',
        'Referer': BASE + '/',
    })
    try:
        jsfuck = r.json()['data']['hashsalt']
        out = node_eval(jsfuck)
        return out if out else ''
    except Exception as e:
        log('  hashsalt 获取失败:', e)
        return ''


def get_free_captcha(sess):
    """领取用验证码：服务端直接返回缺口 puzzle_x，无需图像识别"""
    r = sess.get(BASE + '/ajax_shop.php?act=captcha', headers={
        'Accept': 'application/json',
        'X-Requested-With': 'XMLHttpRequest',
        'Referer': BASE + '/',
    })
    try:
        d = r.json()
        if d.get('puzzle_x') is not None and d.get('token'):
            return d
        return {}
    except Exception:
        return {}


def claim_free(sess, tid, inputvalue, num, cap, hashsalt):
    """提交免费领取。返回 pay 接口 JSON。"""
    r = sess.post(BASE + '/ajax_shop.php?act=pay',
                  data={
                      'tid': str(tid),
                      'inputvalue': inputvalue,
                      'num': str(num),
                      'captcha_token': cap.get('token', ''),
                      'captcha_x': str(cap.get('puzzle_x', '')),
                      'captcha_time': str(random.randint(600, 2000)),
                      'captcha_timestamp': str(int(time.time() * 1000)),
                      'hashsalt': hashsalt,
                  },
                  headers={
                      'Content-Type': 'application/x-www-form-urlencoded',
                      'X-Requested-With': 'XMLHttpRequest',
                      'Accept': '*/*',
                      'Referer': BASE + '/',
                  })
    try:
        return r.json()
    except Exception:
        return {'code': -1, 'msg': r.text[:150]}


# ============================================================
# cookie 缓存 / 状态
# ============================================================
def load_json(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def save_json(path, obj):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def today_str():
    return datetime.now().strftime('%Y-%m-%d')


# ============================================================
# 配置
# ============================================================
def load_config():
    if yaml is None:
        log('✗ 缺少 PyYAML（pip install pyyaml）')
        sys.exit(1)
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            cfg = yaml.safe_load(f)
    except Exception as e:
        log(f'✗ 读取 {CONFIG_FILE} 失败: {e}')
        sys.exit(1)

    platforms = (cfg or {}).get('platforms') or {}
    if not platforms:
        log('✗ config.yaml 未配置 platforms')
        sys.exit(1)

    # 校验：tid 必须在平台 items 内，且在内置 GOODS 中（防打错）
    for pname, pconf in platforms.items():
        if pname not in GOODS:
            log(f'✗ 未知平台 {pname}，可选: {"/".join(GOODS.keys())}')
            sys.exit(1)
        tid = (pconf or {}).get('tid')
        items = (pconf or {}).get('items') or []
        item_tids = [it.get('tid') for it in items if isinstance(it, dict)]
        if tid not in item_tids:
            log(f'✗ 平台 {pname} 的 tid={tid} 不在其 items 列表中，'
                f'items 里的 tid: {item_tids}')
            sys.exit(1)
        if tid not in [g['tid'] for g in GOODS[pname]]:
            log(f'✗ 平台 {pname} 的 tid={tid} 不在支持的商品清单中，'
                f'支持的 tid: {[g["tid"] for g in GOODS[pname]]}')
            sys.exit(1)

    order = list(platforms.keys())
    # priority 来源：环境变量 TA_PRIORITY > config.yaml > 缺省第一个平台
    # （GitHub Actions 用 Repository variable 覆盖，改平台无需提交代码）
    prio = (os.environ.get('TA_PRIORITY', '').strip().lower()
            or cfg.get('priority') or order[0])
    if prio not in platforms:
        log(f'✗ priority={prio} 不在 platforms 中: {order}')
        sys.exit(1)
    chain = [prio] + [p for p in order if p != prio]
    return platforms, prio, chain


# ============================================================
# Telegram 推送
# ============================================================
def tg_send(title, text):
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    chat_id = os.environ.get('TELEGRAM_CHAT_ID', '')
    if not token or not chat_id:
        print(f'\n📣 推送预览（未配置 Telegram）\n【{title}】\n{text}\n')
        return
    try:
        r = requests.post(
            f'https://api.telegram.org/bot{token}/sendMessage',
            json={'chat_id': chat_id, 'text': f'【{title}】\n{text}',
                  'disable_web_page_preview': True},
            timeout=20)
        j = r.json()
        log('Telegram 推送:', '成功' if j.get('ok') else f'失败 {j}')
    except Exception as e:
        log('Telegram 推送异常:', e)


# ============================================================
# 登录（缓存 CK 优先，失效才走滑块+账密）
# ============================================================
def ensure_login(user, pwd):
    """返回 (sess, sign_json, from_cache)。会话保证已登录且已签到。"""
    # 阶段 1：缓存 CK
    cached = load_json(COOKIE_FILE)
    if cached and cached.get('cookies'):
        sess = build_session(cached['cookies'])
        log('  [1/2] 尝试缓存 cookie…')
        q = qiandao(sess)
        if q.get('code') == 0 or '已签到' in (q.get('msg') or ''):
            log(f'  ✓ 缓存有效，签到: {q.get("msg") or "已签到"}')
            return sess, q, True
        log(f'  缓存已失效（{q.get("msg") or q.get("code")}），走账密登录')

    # 阶段 2：账密登录（Playwright 真实浏览器滑块，命中率 ~100%）
    sess = build_session()
    log('  [2/2] 账密登录（Playwright 滑块）')
    cookies, res, _ = pw_login(user, pwd)
    if not cookies or res is None:
        log('  登录失败:', json.dumps(res or {}, ensure_ascii=False)[:120])
        return None, res or {}, False
    # 把浏览器 cookie 注入 requests session
    for k, v in cookies.items():
        sess.cookies.set(k, v, domain='ta.gnx3.cn')
    log('  ✓ cookie 已导入 requests session')

    save_json(COOKIE_FILE, {'cookies': cookie_dict_of(sess), 'saved_at': now()})
    log('  cookie 已缓存')

    warmup_after_login(sess)
    q = {}
    for attempt in range(1, 5):
        q = qiandao(sess)
        if q.get('code') == 0 or '已签到' in (q.get('msg') or ''):
            break
        if '校验失败' in (q.get('msg') or '') and attempt < 4:
            wait = attempt * 2
            log(f'  第 {attempt} 次签到返回校验失败，等 {wait}s 重试')
            time.sleep(wait)
            continue
        break
    return sess, q, False


# ============================================================
# 领取
# ============================================================
def find_goods(platform, tid):
    for g in GOODS[platform]:
        if g['tid'] == tid:
            return g
    return None


def try_claim(sess, platform, tid, dry_run=False):
    """领取单个商品。返回 (status, msg)。status: success/already/fail"""
    g = find_goods(platform, tid)
    if not g:
        return 'fail', f'tid {tid} 不在支持清单'
    if g.get('implemented') is False:
        return 'fail', f'{g["name"]} 暂不实现'
    inputvalue = os.environ.get(ENV_INPUT[platform], '').strip()
    if not inputvalue:
        return 'skip', f'未配置 {ENV_INPUT[platform]}'
    num = g['amount'] if g['amount'] > 0 else 1

    for attempt in (1, 2):  # 验证类失败自动重试一次
        cap = get_free_captcha(sess)
        if not cap.get('puzzle_x'):
            log(f'  [{g["name"]}] 领取验证码获取失败')
            time.sleep(1.0)
            continue
        hashsalt = get_hashsalt(sess)
        if not hashsalt:
            log(f'  [{g["name"]}] hashsalt 获取失败')
            time.sleep(1.0)
            continue
        if dry_run:
            log(f'  [DRY-RUN] 跳过提交: {g["name"]} x={cap.get("puzzle_x")}')
            return 'dryrun', 'DRY-RUN 跳过提交'
        res = claim_free(sess, tid, inputvalue, num, cap, hashsalt)
        code = res.get('code')
        msg = str(res.get('msg', ''))[:60]
        log(f'  [{g["name"]}] pay → code={code} {msg}')
        if code == 1:
            return 'success', msg or '领取成功'
        if code == 2:
            log('  验证未通过，重试…')
            time.sleep(1.5)
            continue
        if MUTEX_PAT.search(msg):
            return 'already', msg or '今日已领取过'
        return 'fail', msg or f'code={code}'
    return 'fail', '验证失败（已重试）'


# ============================================================
# 主流程
# ============================================================
def main():
    log('════════ ta.gnx3.cn 免费领取 + 签到 ════════')
    user = os.environ.get('TA_USER', '').strip()
    pwd = os.environ.get('TA_PASS', '').strip()
    dry_run = os.environ.get('TA_DRY_RUN', '') == '1'
    if not user or not pwd:
        log('✗ 未设置环境变量 TA_USER / TA_PASS')
        sys.exit(1)

    platforms, prio, chain = load_config()
    log('领取尝试链:', ' → '.join(chain), f'（priority={prio}）')

    state = load_json(STATE_FILE) or {}
    if state.get('date') == today_str():
        log(f'今天（{today_str()}）已处理过：'
            f'{state.get("platform")}/{state.get("name")} status={state.get("status")}')
        tg_send('🌾 ta.gnx3.cn 自动任务',
                f'⏭️ 今天已处理过（{state.get("name")}，{state.get("status")}），跳过。')
        sys.exit(0)

    lines = []

    # ---- 登录 + 签到 ----
    sess, sign, from_cache = ensure_login(user, pwd)
    if sess is None:
        err = (sign or {}).get('msg') or '滑块未通过或登录失败'
        lines.append(f'📝 签到/登录: ❌ {err}')
        tg_send('🌾 ta.gnx3.cn 自动任务', '\n'.join(lines))
        sys.exit(2)

    sign_ok = bool(sign) and (sign.get('code') == 0 or '已签到' in (sign.get('msg') or ''))
    sign_state = (sign or {}).get('msg') or ('已签到' if from_cache else '未知')
    lines.append(f'📝 签到: {"✅ " + sign_state if sign_ok else "⚠️ " + sign_state}')

    # ---- 领取（按尝试链降级）----
    # 若当前平台未配置输入（skip），跳过领取环节（只签到，不报错）
    claim_result = None
    claim_lines = []
    for platform in chain:
        tid = platforms[platform]['tid']
        g = find_goods(platform, tid)
        log(f'尝试领取: {platform} / {g["name"]} (tid={tid})')
        status, msg = try_claim(sess, platform, tid, dry_run=dry_run)
        if status == 'skip':
            log(f'  跳过领取：{msg}（只签到，不领取）')
            break
        if status in ('success', 'already', 'dryrun'):
            claim_result = (platform, tid, g['name'], status, msg)
            mark = {'success': '✅ 领取成功', 'already': '⏭️ 今日已领取过（服务端互斥）',
                    'dryrun': '🧪 DRY-RUN'}[status]
            claim_lines.append(f'🎁 领取: {mark} — {platform}·{g["name"]}（{msg}）')
            break
        claim_lines.append(f'⚠️ {platform}·{g["name"]} 失败: {msg}，降级下一平台')
        time.sleep(random.uniform(2, 4))

    lines.extend(claim_lines)
    if not claim_result:
        # 区分：全部 skip（没配输入，只签到）vs 领取失败
        all_skip = all(
            not os.environ.get(ENV_INPUT[p], '').strip()
            for p in chain
        )
        if all_skip:
            lines.append('🎁 领取: ⏭️ 未配置平台输入，跳过（只签到）')
        else:
            lines.append('🎁 领取: ❌ 所有平台均失败（明天可重试）')

    # ---- 余额 ----
    p = profile(sess)
    if p.get('balance'):
        extra = f'（今日收益 {p["today"]} 元）' if p.get('today') else ''
        lines.append(f'💰 余额: {p["balance"]} 元{extra}')

    # ---- 写状态（成功/已领过才写，失败不写以便明天/下次重试）----
    if claim_result:
        platform, tid, name, status, msg = claim_result
        if status in ('success', 'already'):
            save_json(STATE_FILE, {
                'date': today_str(), 'platform': platform, 'tid': tid,
                'name': name, 'status': status, 'saved_at': now(),
            })
        # 更新 cookie 缓存（领取过程会话可能有变化）
        save_json(COOKIE_FILE, {'cookies': cookie_dict_of(sess), 'saved_at': now()})

    log('\n════════ 执行结果 ════════')
    for ln in lines:
        log(' ' + ln)
    tg_send('🌾 ta.gnx3.cn 自动任务', '\n'.join(lines))
    sys.exit(0)


if __name__ == '__main__':
    main()
