# 测量口径

性能数字最容易出错的地方不在优化，在测量。这份文件定口径，前后必须用同一档。

## 三档口径

| 档 | 网络 | CPU | 用途 |
|---|---|---|---|
| 主口径 | Fast 4G：往返 20ms，下行 4Mbps，上行 3Mbps，对所有请求生效（含本地服务） | 不限速 | 结论用这一档 |
| 慢机档 | 同上 | 4 倍限速 | 看低配电脑上的体感 |
| 线上 | 真实网络 | 真实设备 | PageSpeed Insights 的 CrUX 数据，上线后再量一次 |

几条硬规矩：

- **冷缓存**：每次测量用一个新的浏览器上下文。
- **限速必须对所有请求生效**。只限外部 CDN、不限本地服务，那么「把库改成自托管」会显得几乎零耗时，这是假收益。
- **每组至少 10 次**，报 p50 / p75 / p95，不报平均数。行业惯例看 p75（四分之三的访问比它快）。
- **最终对比配对测**：两个版本同时起服务，A、B、A、B 交替跑。机器上有别的任务在跑时，分开时段测出来的前后差，一部分是负载差。

## 用 `scripts/bench.py` 测

```bash
python3 scripts/bench.py \
  --url http://127.0.0.1:8080/ \
  --ready "document.querySelector('textarea') && document.querySelector('textarea').offsetParent !== null" \
  --runs 10 --profile fast4g --cpu 1 --out perf/bench/base.json

# 配对测：两个版本交替
python3 scripts/bench.py \
  --url http://127.0.0.1:8080/ --url-b http://127.0.0.1:8081/ \
  --ready "<同一个判定表达式>" --runs 10 --profile fast4g --out perf/bench/paired.json
```

- `--ready` 是「能用」的判定表达式，页面里每一帧检查一次，第一次为真的时刻记为「能用」。
- `--type-check <选择器>`：判定为真后，再往这个输入框打一个字，确认它真的出现在输入框里，防止拿一个不能用的空壳过关。
- 需要走代理访问外网时加 `--proxy http://<代理地址>`，本地地址会自动绕过代理。
- 输出 JSON 里有每次的原始数据、分位数、LCP、请求数和字节数。

## 线上真实用户数据（CrUX）

```bash
curl -s "https://www.googleapis.com/pagespeedinsights/v5/runPagespeed?url=<URL>&strategy=mobile" \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print(json.dumps(d.get('loadingExperience',{}).get('metrics',{}), indent=1))"
```

流量小的站可能没有 CrUX 数据，如实写「没有」，不要拿 Lighthouse 分数顶替真实用户数据。

**量线上之前先量一个对照站**。从自己电脑上 curl 或用浏览器量线上，数字里混着测量机自己的网络：走代理、跨境、公司网关，都会让所有网站的首字节时间一起变慢。做法是同一时间把一个公认很快的站（比如 vercel.com，或者同一家托管商的官网）一起量，拿到的数和你的站差不多，就说明慢在你这边的网络，不在网站。实测踩过：测量机经代理访问，所有站的首字节时间都在 1.4 秒上下，连 vercel.com 自己也是，差点把「优化 TTFB」当成了主攻方向。真实用户的首字节时间，以 CrUX 的 TTFB 为准（它把重定向也算在内）。

**上线前先量一次线上旧版**。上线后用同一台机器、同一条网络、同一个判定表达式量新版，这样才有一组线上的前后对比。判定表达式要选两个版本都有的终点（比如框架挂载完成），新版额外的终点（比如静态外壳能输入）另外单独量。

## 首屏拆解

在页面里读 `performance.getEntriesByType('resource')`，按 `responseEnd` 排序，标出：

- 同步 `<script>`、阻塞渲染的 CSS：它们下载完之前页面画不出来
- 下载完成时间最晚的那几个：它们通常就是首屏时间本身
- 重定向：apex 域名 308 到 www 这类跳转会白白多一个往返

## 确定性计数

真实耗时适合当结论，不适合当每一步的闸门。每一步先看这些计数有没有降：

- 首屏关键路径上的请求数
- JS / CSS / 图片 / 字体的传输字节数
- 主线程长任务（>50ms）的总时长：`PerformanceObserver({type:'longtask'})`
- 跟产品相关的计数：每次按键的渲染次数、样式写入次数、`querySelectorAll` 次数，在测量台里 monkey-patch 计数，不改源码

纯 JS 热路径还可以数 CPU 指令：`valgrind --tool=callgrind node --predictable bench.js`，跑一次就是确定的数。

每个代理计数都要证明自己：至少两次改动里，计数降了，真实耗时也降了。

## 报告的写法

| 操作 | 口径 | 优化前 p75 | 优化后 p75 | 降幅 | 配对测量时段 |
|---|---|---|---|---|---|

后面附每一步的贡献（哪一步省了多少毫秒），以及没达标的原因。
