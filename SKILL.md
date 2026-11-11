---
name: amazon-asin-image-history
description: 追溯亚马逊 ASIN 的图片更换记录（主图与各张附图的历史版本、每次变化的时间窗口、换图前后原图），基于 Internet Archive 归档快照还原。用于回答“这个链接什么时候换过图”“换图前后长什么样”“主图换过几次”。
---

# 亚马逊 ASIN 图片变更追溯

用 Internet Archive 的历史快照还原某个 ASIN 商品页在不同时刻呈现的图片组，输出**版本序列 + 换图时间窗口 + 前后原图**。

## 先说清能力边界（必须写进给用户的结论里）

| 能拿到 | 拿不到 |
|---|---|
| 每次变化发生在**哪两个快照之间**（时间窗口） | 卖家后台的真实换图时间戳 |
| 每个版本的主图 + 每张附图的**原图**与图片 ID | 两次快照之间被换掉又换回的版本 |
| 图片数量、顺序、位置（MAIN/PT01…） | 归档从未抓取过的区间 |
| 页面标题与父 ASIN 的同步变化 | 视频、A+ 内容的变更 |

因此结论只能用**“X 到 Y 之间”**表述，绝不能说成“某日换的”。

## 执行流程

```bash
# 1) 用 load_workspace_dependencies 返回的 python 解释器
python .dsh/skills/amazon-asin-image-history/scripts/asin_image_history.py \
  --asin B0CG1LGWR6 --site amazon.com
```

脚本五步：CDX 列快照 → 抓 `id_` 原始 HTML → 解析 `colorImages` → 归并版本 → 下载图片并生成报告。

产物在 `asin_history/<ASIN>/`：

| 文件 | 用途 |
|---|---|
| `report.html` | 逐版本对照表，含主图/附图缩略图与原图链接 |
| `contact_sheet.png` | 一张图看完所有版本（先看这张） |
| `summary.txt` | 纯文本时间轴与换图窗口 |
| `versions.json` | 机器可读，含 UTC 快照时间与图片 ID |
| `pics/` | 下载的原图 |

常用参数：

- `--all`：全量抓取（默认每月首末采样）。**要精确窗口就必须用 `--all`**；采样会拉大窗口。
- `--max-snapshots N`：只用于快速摸底，不要用于出结论。
- `--site amazon.co.uk` 等：换站点。
- `--tz`：输出时区，默认 `Asia/Shanghai`。

## 判读规则（这是本 skill 的核心）

1. **图片 ID 是内容寻址的**。`m.media-amazon.com/images/I/<ID>` 的 ID 由图片内容决定，ID 变即内容变，可直接当作“换图”的硬证据。
2. **看 `variant` 字段而不是数组下标**。`MAIN` / `PT01`… 是页面位置；同一批图只是顺序调整时，逐个对比位置会误报“更换”，必须先比 ID 集合再看位置。
3. **图片数量在 8/9 之间波动**多为渲染差异或视频槽位，不必然是增删图；必须打开图确认。
4. **单点异常**（前后版本相同、中间一个快照不同，随后又恢复）按“未复现”处理，标注“疑似 A/B 测试或临时替换”，不计入换图次数。
5. **`parent_asin` 变化**说明变体组被重组，此时整组图可能被重建，应单独说明，不要与普通换图混为一谈。
6. **报告里的“解析成功 N / 总数 M”必须转述给用户**。验证码页占比通常在一半左右；成功的太少时结论可信度要打折扣。
7. 首个可解析快照之前的版本**未知**，不要假设它就是首发图。

## 已知坑（照做可省大量时间）

- **CDX 必须用 `matchType=prefix`**。只查精确 URL 会漏掉 90% 带 `ref`/`tag` 参数的快照（本例 20 条 vs 174 条）。
- **必须取 `id_` 原始 HTML**。普通 `/web/<ts>/` 版本会被 Wayback 注入工具栏并转成文本，解析不到 `colorImages`。
- **响应是 gzip**，需 `gzip.decompress`（判断 `b'\x1f\x8b'`）。
- **`colorImages` 是 JS 对象字面量**（单引号、含 `A.$.parseJSON(...)` 变体），`json.loads` 会失败，用正则按 `},{` 切块后逐块取 `hiRes` + `variant`。
- **本机 TLS 有中间人证书**：`ssl.CERT_NONE` + `check_hostname=False`，否则 `CERTIFICATE_VERIFY_FAILED`。
- **`curl.exe` 在此环境报 `SEC_E_NO_CREDENTIALS`**，`node` 可能不在 PATH；用 bundled python 的 `urllib`。
- **Wayback 会 429**：请求间隔 ≥1.5s，429 时指数退避重试。
- **大量快照是 Amazon 验证码页或 “Continue shopping” 跳转页**：只有 HTML 里出现 `colorImages` 才算有效样本。
- **快照时间戳是 UTC**，输出前必须转目标时区，否则日期会差一天。
- 老图片 ID 在 `m.media-amazon.com` 上通常仍可访问，但可能 404，下载失败要在报告里保留 ID 供人工核对。

## 输出给用户的结构

```
结论：主图 N 个版本（换过 N-1 次），附图 M 次变动。

| 主图版本 | 首次观测 | 上次观测到旧版 |
|---|---|---|
| <ID> | YYYY-MM-DD | YYYY-MM-DD |

换图窗口：
1. A → B 之间（约 X 个月，空档大，无法精确定位）
2. C → D 之间

三个必须说明的限制：
- 首个可解析快照之前未知
- 精确到天做不到，只能给区间
- 单点异常/疑似 A/B 已单独标注

来源：Internet Archive CDX + 商品页 colorImages 解析；图片取自 m.media-amazon.com
```

## 禁止

- 不得把快照区间写成确切换图日期。
- 不得在没有快照的区间里推断“应该是那时候换的”。
- 不得把 A/B 测试、渲染差异、顺序调整算作换图。
- 不得在解析成功率过低时给出确定性结论。
- 公开渠道确实拿不到时，直接说拿不到，并说明需要卖家后台/第三方监控工具。
