# epub-illustrate

给电子书配上真实的图片，**不改动原文一个字**。

读传记、商业史这类书时，书里每出现一个新人物、新产品，脑子里往往没有画面。这个 Agent Skill 会通读整本 EPUB，找出书中的人物、产品、公司、地点和事件，从网上搜集真实的照片、旧网页存档和产品截图，然后：

- **正文插图**：在它们第一次出现的那一段后面插入一张小图，并附图注"名字 · 身份"，第二行可写年份或说明；
- **章末图集**：每章末尾加一页「本章人物与产品」，集中展示本章出现过的对象。

两种形式可以都要，也可以只要其中一种。

[English](#english)

## 安装

```bash
npx skills add wavever/epub-illustrate
```

支持 Claude Code 等兼容 [Agent Skills](https://skills.sh/) 的工具。只装到当前用户：`npx skills add wavever/epub-illustrate -g`。

依赖如下：

- **Python 3.9+**：运行 `pip install -r skills/epub-illustrate/requirements.txt`，会装上 Pillow、lxml、certifi。
- **Google Chrome 或 Chromium**：用于在后台访问百度百科，以及生成预览图。
- **Java**：用于 epubcheck 格式校验。首次使用时会自动下载 epubcheck 到 `~/.cache/epub-illustrate/`。

## 使用

对 Agent 说：

> 给这本书配插图：~/Books/腾讯传.epub

开始之前，Agent 会先和你确认三件事。每一项都有预设方案，你也可以直接描述自己的方案。

| 问题 | 预设方案 |
|---|---|
| 图片怎么找 | **后台检索**（推荐，不占用你的浏览器）/ 使用你的浏览器（多 Bing，可过验证码）/ 只用开放图库（Wikidata、Commons）/ 自定义 |
| 插图放在哪里 | **正文 + 章末图集** / 只在正文插图 / 只加章末图集 |
| 配图范围 | **所有新出现的对象** / 只配主要人物和核心产品 / 自定义 |

之后的流程：

1. 拆书并建立段落索引；
2. 逐章列出要配图的对象；
3. 从多个来源搜集候选图；
4. 拼成预览图挑选；
5. 生成新书；
6. 校验。

先做一章样章给你看，确认后再做全书。

## 图片来源

| 来源 | 用途 | 方式 |
|---|---|---|
| 本地文件夹 | 你已有的素材 | 直接复制 |
| Wikidata / Wikimedia Commons | 外国人物、地点、软件截图 | 公开 API |
| 百度百科 | 中文人物、公司、产品 | 后台无头 Chrome（独立的临时配置，不碰你的浏览器） |
| 360 图片搜索 | 老照片、旧版界面、旧网页 | 直接请求 |
| Bing 图片搜索 | 同上 | 仅在「使用你的浏览器」方案下，通过 ego-browser 调用 |

## 规则与保证

- **原文零改动**：插入的内容一律放在原有元素之间，并用 `<!--illus:start-->…<!--illus:end-->` 包起来。`verify.py` 会去掉这些块，逐字节比对原书的每个文件。
- **格式不变差**：`epubcheck_diff.py` 只报告新增的校验问题。很多市售或转换来的电子书本身就有大量校验错误，这些不算在内。
- **只用真实图片**：照片、扫描件、截图、网页存档都可以，不使用 AI 生成的图片。
- **身份核实**：只有至少两个独立来源都对得上同一张脸，才会标注人物姓名。不确定的宁可不配，或者只写确定的内容，比如只写"新浪网的早期标识"，不写画面里的人是谁。
- **优先年代对应的图**：比如年轻时的创始人、当年的产品界面、旧网站首页。用今天的照片时，会在图注里注明"今貌"。
- 每本书都会输出一份 `配图清单.md`，列出每张图放在哪一段、图注和来源，以及暂时没配上图的对象。

## 注意

- 只支持**没有 DRM** 的 EPUB。遇到 DRM 保护的书，Skill 会停下来，不会尝试解除。
- 搜集到的图片版权归原作者所有。本工具面向个人阅读，生成的电子书请勿公开传播。使用时请遵守各网站的服务条款。

## 目录结构

```
skills/epub-illustrate/
├── SKILL.md            # 给 Agent 的说明：确认方案 → 拆书 → 列对象 → 找图 → 挑图 → 生成 → 校验
├── requirements.txt
└── scripts/
    ├── extract.py      # 解包 EPUB，建立段落索引
    ├── find_images.py  # 多来源搜集候选图
    ├── webfetch.py     # 后台 Chrome（百度百科）/ 360 图片搜索
    ├── baike.mjs, bing.mjs  # 「使用你的浏览器」方案（ego-browser）
    ├── sheet.py        # 候选图拼成预览图
    ├── inject.py       # 插入正文插图和章末图集，并重新打包
    ├── verify.py       # 逐字节验证原文未改
    ├── epubcheck_diff.py    # 只报告新增的格式问题
    └── preview.py      # 渲染预览页
```

---

## English

**epub-illustrate** is an Agent Skill that adds real-photo illustrations to an EPUB without changing a single character of the original text. It finds each new person, product, company, place and event in the book, collects real photos, archived web pages and period screenshots, and inserts a captioned figure after the paragraph where it first appears and/or a per-chapter "people & products" gallery.

```bash
npx skills add wavever/epub-illustrate
```

Before starting, the agent asks how to source images, where to place them, and how dense the coverage should be. Each question has presets, and you can also describe your own approach. Every insertion is wrapped in marker comments, and `verify.py` checks that the book is byte-identical once those are removed. Only DRM-free EPUBs are supported. The images belong to their owners, so the tool is meant for personal reading.

## License

[MIT](LICENSE)
