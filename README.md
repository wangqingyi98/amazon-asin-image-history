# amazon-asin-image-history

追溯亚马逊 ASIN 的图片更换记录——主图与每张附图的历史版本、每次变化的时间窗口、换图前后的原图。

基于 Internet Archive（Wayback Machine）的公开归档快照还原，不需要卖家后台账号。

## 它解决什么问题

“这个 listing 什么时候换过图？”“主图换过几次？”“换之前长什么样？”

亚马逊不公开图片修改历史。但 Wayback Machine 保存了大量商品页快照，每个快照里都内嵌了当时的完整图片组（`colorImages`，含 `MAIN` / `PT01`… 位置标记）。把不同时间的快照对齐比对，就能还原出版本序列和换图窗口。

## 能拿到 / 拿不到

| 能拿到 | 拿不到 |
|---|---|
| 每次变化发生在哪两个快照之间（时间窗口） | 卖家后台的真实换图时间戳 |
| 每个版本的主图 + 每张附图的原图与图片 ID | 两次快照之间换掉又换回的版本 |
| 图片数量、顺序、位置（MAIN/PT01…） | 归档从未抓取过的区间 |
| 页面标题与父 ASIN 的同步变化 | 视频、A+ 内容的变更 |

所以结论只能用 **“X 到 Y 之间”** 表述，不能写成“某日换的”。

## 用法

需要 Python 3.9+；生成拼图需要 Pillow（可选）。

```bash
python scripts/asin_image_history.py --asin B0CG1LGWR6 --site amazon.com
```

输出到 `asin_history/<ASIN>/`：

| 文件 | 内容 |
|---|---|
| `report.html` | 逐版本对照表，含缩略图与原图链接 |
| `contact_sheet.png` | 一张图看完所有版本 |
| `summary.txt` | 时间轴与换图窗口 |
| `versions.json` | 机器可读，含 UTC 快照时间与图片 ID |
| `pics/` | 下载的原图 |

常用参数：

| 参数 | 说明 |
|---|---|
| `--all` | 全量抓取。要精确窗口必须用它；默认只做每月首末采样 |
| `--max-snapshots N` | 只抓 N 个均匀分布的样本，用于快速摸底 |
| `--site` | 站点，默认 `amazon.com`，可换 `amazon.co.uk` 等 |
| `--tz` | 输出时区，默认 `Asia/Shanghai` |
| `--sleep` | 请求间隔秒数，默认 2.0，被限流时调大 |

## 判读规则

1. **图片 ID 是内容寻址的**。`m.media-amazon.com/images/I/<ID>` 的 ID 由内容决定，ID 变即内容变，可直接当“换图”的硬证据。
2. **看 `variant` 而不是数组下标**。`MAIN` / `PT01`… 是页面位置；只是顺序调整时，按下标比对会误报“更换”。
3. **8/9 张之间的波动**多为渲染差异或视频槽位，不必然是增删图。
4. **单点异常**（前后版本相同、中间一个不同、随后恢复）按“未复现”处理，疑似 A/B 测试，不计入换图次数。
5. **`parentAsin` 变化**说明变体重组，整组图可能被重建，应与普通换图分开说明。
6. **报告里的“解析成功 N / 总数 M”要一起看**。验证码页占比通常在一半左右。
7. 首个可解析快照之前的版本**未知**，不要假设它是首发图。

## 已知坑

- CDX 查询必须用 `matchType=prefix`，否则漏掉约 90% 带 `ref`/`tag` 参数的快照
- 必须取 `id_` 原始 HTML，普通 `/web/<ts>/` 版本会被注入工具栏并转成文本
- Wayback 响应是 gzip，需自行解压
- `colorImages` 是 JS 对象字面量（单引号、可能包在 `A.$.parseJSON(...)` 里），不能用 `json.loads`
- 部分环境有 TLS 中间人证书，需要关闭校验
- Wayback 会返回 429，请求间隔建议 ≥1.5s 并做退避
- 大量快照是亚马逊验证码页，只有 HTML 里出现 `colorImages` 才算有效样本
- 快照时间戳是 UTC，输出前要转目标时区

## 作为 DSH Skill 使用

`SKILL.md` 是 [DeepSeek Harness](https://github.com/) 的 skill 定义。放到 `<project>/.dsh/skills/amazon-asin-image-history/` 或 `~/.dsh/skills/amazon-asin-image-history/`，agent 会在遇到“查 ASIN 换图记录”这类请求时自动加载。

## 免责

只读取 Internet Archive 的公开归档，不访问卖家后台、不绕过任何登录或反爬机制。归档覆盖率因 ASIN 而异，查不到是常态，工具会如实报告而不是猜测。

## License

MIT
