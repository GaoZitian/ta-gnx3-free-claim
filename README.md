# 🌾 ta-gnx3 免费福利区自动领取

针对 `https://ta.gnx3.cn`（PHP 刷赞刷粉平台）的**每日自动任务**，自动完成：**过滑块登录 → 每日签到 → 免费福利区领取一个免费商品 → Telegram 通知**。

> ⚠️ **重要范围说明（务必先读）**
> 
> 站点「免费福利区」里其实有**很多**免费商品（除下方列出的外，还有大量优惠券类免费品，如滴滴/饿了么/肯德基/美团等）。
> **本脚本只支持下方 `config.yaml` 里列出的商品**，其余免费商品一律**不做考虑、不实现、不涉及**。
> 这不是「没做全」，而是只针对这些作品/账号类的免费业务，优惠券类不在范围内。
> 其中 **QQ 说说浏览量（tid 5186）暂不实现**，其余 12 个均可正常领取。

---

## 📑 目录

- [一、它做什么](#sec-1)
- [二、互斥规则](#sec-2)
- [三、领取顺序（降级规则）](#sec-3)
- [四、支持的免费商品清单](#sec-4)
- [五、配置方法](#sec-5)
- [六、环境变量](#sec-6)
- [七、本地运行](#sec-7)
- [八、GitHub Actions 定时运行](#sec-8)
- [九、技术要点](#sec-9)
- [十、目录结构](#sec-10)
- [十一、安全与边界](#sec-11)

---

<a id="sec-1"></a>

## 📋 一、它做什么

每天运行一次，按顺序执行：

- ✅ **登录**（Playwright 真实浏览器过拼图滑块）
    - 优先用**缓存的 cookie**（约 7 天有效），cookie 失效才走滑块 + 账号密码登录。
- ✅ **签到**（领取每日签到积分）
- ✅ **免费领取**（`ajax_shop.php?act=captcha` → `act=pay`，服务端直接返回缺口坐标，无需人工识别）
    - **未配置对应平台输入（如 `TA_DOUYIN_INPUT` 为空）时，领取步骤直接跳过，只执行签到，不报错。**
- ✅ **Telegram 通知**（领取结果、余额、今日收益）——**可选**：没配 `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` 时只打印推送预览，不影响签到/领取

---

<a id="sec-2"></a>

## 🔄 二、互斥规则（核心，站点强制）

**站点规定：免费福利区的所有免费商品每天全局只能成功领取一个。** 领了其中一个，当天其余全部领不了。

脚本实现 **双重互斥**：

1. **本地状态文件** `.cache/state.json`：记录 `{date, platform, tid, name, status}`。
    每天第一次运行，`date == 今天` 就直接跳过（防脚本重复领），跨天自然重置。
2. **服务端互斥兜底**：即使本地状态丢失，服务端也会拒绝当天第二个免费领取（返回「已领过」类错误），脚本收到后记录 `status: already` 并停止。

**关键**：**每天最多只成功领取一个**，成功后就写状态，当天不再尝试任何其他商品。

---

<a id="sec-3"></a>

## 🔀 三、领取顺序（降级规则）

`priority`（优先入口）决定**每天先领哪个平台**；领取失败自动**降级到下一个已配置平台**（按 `platforms` 定义顺序），第一个成功的就停。

**`priority` 来源（优先级从高到低）**：

1. 环境变量 `TA_PRIORITY` ← **GitHub Actions 用 Repository variable 改，无需提交代码**
2. `config.yaml` 的 `priority`
3. 都不填则默认取第一个平台（`douyin`）

- **每平台只配置一个 `tid`**（当前领取项），平台内没有「下一个商品」可降级——失败直接换平台。
- 降级链示例（`priority: douyin`，三个平台都配了）：
    `douyin(5564)` → 失败 → `kuaishou(2170)` → 失败 → `qq(5700)` → 失败 → 全部失败，报告，明天重试。
- **为什么平台内不设多个商品**：服务端互斥是全局的，同一平台内换商品也大概率被「已领过」拒绝，收益为零，故只配一个 `tid`。
- **`priority` 不填**：默认取第一个平台（`douyin`）。

---

<a id="sec-4"></a>

## 🎁 四、支持的免费商品清单（12 个可领取 + 1 个暂不实现）

### 抖音（`douyin`，需提供作品链接）

| tid | 商品 | 每日数量 | 输入 |
| --- | --- | --- | --- |
| 5564 | 作品点赞10-1万 | 10 | 作品链接 |
| 5539 | 作品收藏10-1万 | 10 | 作品链接 |
| 5540 | 作品分享100-1万 | 100 | 作品链接 |
| 3140 | 作品播放1千-10万 | 1000 | 作品链接 |

### 快手（`kuaishou`，需提供作品链接）

| tid | 商品 | 每日数量 | 输入 |
| --- | --- | --- | --- |
| 2170 | 作品点赞10-1万 | 10 | 作品链接 |
| 5570 | 福利粉丝10-1万 | 10 | 作品链接 |
| 5535 | 作品收藏10-1万 | 10 | 作品链接 |
| 5536 | 作品播放1百-1万 | 100 | 作品链接 |
| 5537 | 作品分享1百-1万 | 100 | 作品链接 |

### QQ（`qq`，需提供 QQ 号码）

| tid | 商品 | 每日数量 | 输入 | 备注 |
| --- | --- | --- | --- | --- |
| 5700 | 豪华代挂等级加速1天 | - | QQ 号码 |  |
| 5542 | 名片赞1百-10万随机 | 1 | QQ 号码 |  |
| 5379 | 空间访客1百-10万随机 | 1 | QQ 号码 |  |
| 5186 | 说说浏览量1百-100万随机 | 1 | QQ 号码 | ⚠️ 暂不实现 |

> 12 个可领取商品全部 `price=0.00`，全部互斥，每天全局只能成功领一个。
> **站点免费福利区里还有其他免费商品（优惠券类），本脚本一律不做考虑。**

---

<a id="sec-5"></a>

## ⚙️ 五、配置方法（`config.yaml`）

```yaml
platforms:
  douyin:
    tid: 5564                    # ← 当前领哪个（必须在该平台 items 列表内）
    items:
      - {tid: 5564, name: 作品点赞10-1万,   amount: 10}
      - {tid: 5539, name: 作品收藏10-1万,   amount: 10}
      - {tid: 5540, name: 作品分享100-1万,  amount: 100}
      - {tid: 3140, name: 作品播放1千-10万, amount: 1000}
  kuaishou:
    tid: 2170
    items:
      - {tid: 2170, name: 作品点赞10-1万,   amount: 10}
      - {tid: 5570, name: 福利粉丝10-1万,   amount: 10}
      - {tid: 5535, name: 作品收藏10-1万,   amount: 10}
      - {tid: 5536, name: 作品播放1百-1万,  amount: 100}
      - {tid: 5537, name: 作品分享1百-1万,  amount: 100}
  qq:
    tid: 5700
    items:
      - {tid: 5700, name: 豪华代挂等级加速1天}
      - {tid: 5542, name: 名片赞1百-10万随机}
      - {tid: 5379, name: 空间访客1百-10万随机}
      - {tid: 5186, name: 说说浏览量1百-100万随机, status: 暂不实现}

priority: douyin                 # 每天先领这个平台；不填默认取第一个（douyin）
```

**改领什么**：只改对应平台的 `tid`（从上面的清单表格复制）。
**改优先平台**：改 `priority`（`douyin` / `kuaishou` / `qq`），或**在 GitHub Actions 里设 `TA_PRIORITY` 变量**（免提交）。

> 💡 **推荐**：优先平台用 `TA_PRIORITY` 环境变量控制（GitHub repo variable），比提交改 `config.yaml` 更灵活——想换平台时只需改 variable，不用重新推送。
>
> **`TA_PRIORITY` 支持两种格式**（一个变量同时搞定平台+商品）：
>
> - 纯平台：`TA_PRIORITY=douyin` → 只选优先平台，商品用 `config.yaml` 里该平台的 `tid`
> - 平台:商品：`TA_PRIORITY=douyin:5564` → 同时指定优先平台**和**商品 tid（如 `douyin:5564` 领作品点赞、`qq:5542` 领名片赞），无需改 `config.yaml`
>
> 覆盖规则：`TA_PRIORITY` 里 `:` 后的商品 tid **只覆盖对应平台**，降级链里的其他平台仍用各自 `config.yaml` 的 `tid`。若指定了平台不存在的 tid 或非法 tid，脚本启动时报错并列出合法值。

启动时脚本会校验：`tid` 必须在该平台 `items` 列表内，且在内置支持清单里，否则报错并列出合法值。**若选了标记 `status: 暂不实现` 的商品，脚本会直接提示该商品暂不实现。**

---

<a id="sec-6"></a>

## 🔑 六、环境变量（GitHub Actions Secrets / 本地环境）

<table>
<thead>
<tr>
<th width="180">变量</th>
<th width="90">必填</th>
<th>说明</th>
</tr>
</thead>
<tbody>
<tr><td><code>TA_USER</code></td><td>✅</td><td>站点登录账号</td></tr>
<tr><td><code>TA_PASS</code></td><td>✅</td><td>站点登录密码</td></tr>
<tr><td><code>TA_PRIORITY</code></td><td>否</td><td>优先平台（<code>douyin</code> / <code>kuaishou</code> / <code>qq</code>），覆盖 config.yaml 的 <code>priority</code>；也支持 **<code>平台:商品tid</code>** 格式（如 <code>douyin:5564</code>）同时指定优先平台和商品；不填则用 config 或默认第一个平台</td></tr>
<tr><td><code>TA_DOUYIN_INPUT</code></td><td>否</td><td>抖音作品链接（如 <code>https://www.douyin.com/video/xxx</code>）；只填当前会领到的平台，全空则只签到</td></tr>
<tr><td><code>TA_KUAISHOU_INPUT</code></td><td>否</td><td>快手作品链接（如 <code>https://www.kuaishou.com/short-video/xxx</code>）；只填当前会领到的平台，全空则只签到</td></tr>
<tr><td><code>TA_QQ_INPUT</code></td><td>否</td><td>QQ 号码（纯数字，如 <code>10001</code>）；只填当前会领到的平台，全空则只签到</td></tr>
<tr><td><code>TELEGRAM_BOT_TOKEN</code></td><td>否</td><td>Telegram Bot Token（通知）；不配则只打印推送预览，不影响签到/领取</td></tr>
<tr><td><code>TELEGRAM_CHAT_ID</code></td><td>否</td><td>Telegram 会话 ID（通知）；不配则只打印推送预览，不影响签到/领取</td></tr>
<tr><td><code>TA_DRY_RUN</code></td><td>否</td><td><code>1</code> = 只验证登录/滑块/领取链，**不真正提交领取**</td></tr>
</tbody>
</table>

> **注意**：`TA_DOUYIN_INPUT` / `TA_KUAISHOU_INPUT` / `TA_QQ_INPUT` 只需填**当前会领到的平台**所需的那个（按 `priority` 和降级链）。未用到的平台输入可不填。
> **若所有平台输入都为空**：领取步骤直接跳过，**只执行签到，不报错**（通知里显示「⏭️ 未配置平台输入，跳过」）。

---

<a id="sec-7"></a>

## 🚀 七、本地运行

```bash
pip install -r requirements.txt   # requests + Pillow + PyYAML + numpy + playwright
python -m playwright install chromium   # 首次需下载 Chromium（滑块用真实浏览器拖动）

export TA_USER='你的账号'
export TA_PASS='你的密码'
export TA_DOUYIN_INPUT='https://www.douyin.com/video/xxx'   # 或快手/QQ
export TELEGRAM_BOT_TOKEN='xxx'
export TELEGRAM_CHAT_ID='xxx'

# 首次先 dry-run，只验证登录 + 滑块 + 领取链，不真正提交
TA_DRY_RUN=1 python claim.py

# 确认无误后正式跑
python claim.py
```

---

<a id="sec-8"></a>

## ⏰ 八、GitHub Actions 定时运行

`.github/workflows/claim.yml`：cron `10 16 * * *`（UTC 16:10 = 北京时间 00:10），每天一次。

**需配置仓库 Secrets**（Settings → Secrets and variables → Actions → New repository secret）：

- `TA_USER`、`TA_PASS`（✅ 必填）
- `TA_DOUYIN_INPUT` / `TA_KUAISHOU_INPUT` / `TA_QQ_INPUT`（按需，只填当前会领到的平台；全空则只签到不领取）
- `TELEGRAM_BOT_TOKEN`、`TELEGRAM_CHAT_ID`（**可选**：不配则只打印推送预览，不影响签到/领取）

> 💡 **想改领哪个商品**：设 `TA_PRIORITY` 变量（Settings → Variables）即可，支持 `douyin`（纯平台）或 `douyin:5564`（平台:商品）格式，无需改代码。若设了 `TA_PRIORITY`，则 Secrets 里的 `TA_DOUYIN_INPUT` 等需对应填新平台的输入。

**cookie/状态缓存**：`.cache/cookies.json`（登录态，约 7 天）和 `.cache/state.json`（当天互斥状态）通过 `actions/cache` 跨运行保留。

- cookie 有效期内直接复用，**不用每次过滑块**；
- cookie 过期（服务端 session 失效）自动重新滑块 + 账号密码登录。

> **手动测试**：仓库 Actions → 左侧点 `ta-gnx3 free claim` → `Run workflow`。默认会完整执行（领取）。
> 想只做登录/滑块验证不真正领取：在仓库 Settings → Variables 里加 `DRY_RUN_ON_DISPATCH = 1`，再 Run workflow。

---

<a id="sec-9"></a>

## 🔬 九、技术要点（为什么能自动化）

### 1. 登录滑块（Playwright 真实浏览器）

站点登录强制拼图滑块验证。**初版用纯 requests 模拟提交，实测单次命中率仅约 12%**（服务端对 x 容差极小——`x` 保留两位小数——且背景图多为纯暗色，暗像素定位不可靠；错误 x 和正确 x 都返回 4601，无法靠响应区分）。**因此改为 Playwright 真实浏览器拖动**，实测命中率 ~100%（6/6）：

- **`sec_defend` 反爬 cookie**：Playwright 加载登录页时浏览器自动带出，无需手动处理（Node/JSFuck 求值方案保留在代码中作兜底）。
- **缺口识别** `_ncc_x`：**NCC 模板匹配**——拼图块 `piece` 的 RGB 图案 = 缺口处的背景图案（块是从背景抠出来的），在背景缺口 y 行带内滑动，用 alpha>200 掩码算归一化互相关，最高处即缺口左边缘 x（0..264）。比「暗像素占比」精确。
- **拟人拖动** `_drag_human`：ease-out 缓动（先快后慢）+ 随机微抖 + 随机步数（28~42 步），模拟真实鼠标轨迹。
- **登录提交**：滑块通过后 `user-auth.js` 自动把 `ticket` 注入 `POST user/ajax.php?act=login`（`{user, pass, ds_slide_ticket}`）。**注意**：登录端点是 `user/ajax.php?act=login`（页面表单 action），不是站点其他登录入口。
- **滑块失败重试**：最多 3 次，每次失败后**刷新页面**重置表单（滑块失败后 submit 按钮会被禁用 `busy`，必须刷新重填）。
- **cookie 导出**：登录成功后**等页面跳转完成再导出**浏览器全部 cookie，注入 requests session 继续签到/领取。（修复：此前 `json()` 读取成功就立即导出，页面未跳转完会缺 `sec_defend`/`user_token` 关键 cookie → 签到「校验失败」。现在等 URL 离开 `login.php` 再导出。）

### 2. 免费领取（`act=captcha` → `act=pay`）

站点免费领取**用另一套滑块**（`ajax_shop.php?act=captcha`），这套滑块**服务端直接返回缺口坐标**：

```
GET act=captcha → {background, puzzle, puzzle_x, puzzle_y, token}
```

`puzzle_x` 就是缺口 x。直接用它做 `captcha_x` 提交即可，**无需图像识别**。

```
POST act=pay  {tid, inputvalue, num, hashsalt, captcha_token, captcha_x, captcha_time, captcha_timestamp}
```

返回：`code==1` = 领取成功（免费直接成功）；`code==2` = 验证未通过重试；`code==0` = 跳付款（付费，免费品不应出现）；其他 = 错误（`msg` 里可能含「已领过/明天/每日」等互斥文案）。

### 3. `hashsalt`

`ajax_shop.php?act=hashsalt` 返回 **JSFuck 混淆表达式**，需用 **node** 求值得到字符串（同 sec_defend）。

### 4. cookie 持久化

登录成功后保存 `.cache/cookies.json`。`mysid` cookie 有效期约 7 天（服务端 `Max-Age=604800`），`sec_defend` 当天有效（每天重新 GET login.php 一次，不影响已登录的 mysid）。
下次运行先用缓存 cookie 验证登录态（`qiandao` 返回 `code:0`=有效），无效才重新滑块登录。

### 5. 签到

`GET user/ajax_user.php?act=qiandao`。**登录接口会重置会话**，故登录后必须先走一遍 `login.php → user/ → user/qiandao.php` 预热页面再签到，否则返回「校验失败，请刷新页面重试」。（补充：签到「校验失败」的另一根因已修复——登录后 cookie 导出时机，见上「cookie 导出」条。warmup 预热 + 完整 cookie 两者齐备才能签到成功。）

---

<a id="sec-10"></a>

## 📂 十、目录结构

```
ta-gnx3-free-claim/
├── claim.py                 # 主脚本（登录+滑块+签到+领取+通知）
├── config.yaml              # 商品配置（免费品清单 + tid + priority，含暂不实现标注）
├── requirements.txt         # requests / Pillow / PyYAML
├── .cache/                  # 运行时生成（cookie + 状态，git 忽略）
│   ├── cookies.json
│   └── state.json
└── .github/workflows/
    └── claim.yml            # GitHub Actions 每日定时 + 手动触发
```

---

<a id="sec-11"></a>

## 🛡️ 十一、安全与边界

- ✅ **凭证**：账号密码、作品链接、Telegram 均走环境变量 / Secrets，**不写进代码或提交**。
- ✅ **免费**：只领取 `price=0.00` 的免费商品，绝不触发任何付费操作（`act=pay` 只用于免费品）。
- ✅ **互斥**：每天全局只领一个，靠本地状态 + 服务端互斥双保险。
- ✅ **不重试付费**：`act=pay` 免费领取若返回错误不盲目重试（避免重复扣减，虽然免费品无扣款，仍保持克制）。
- ✅ **站点免费福利区范围外商品**：优惠券类等一律不做考虑（见顶部范围说明）。
