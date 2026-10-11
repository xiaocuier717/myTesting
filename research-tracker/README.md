# AI + CAD 测试研究追踪

每天自动抓取 AI × CAD（含测试 / 评测 / 验证、CAD 生成、B-rep、EDA 芯片验证）最新论文，并发布为静态网页。

- `scraper.py`：从 arXiv API 与 OpenAlex 抓取，按关键词过滤相关性、打标签、去重，合并写入 `site/data/papers.json`（只用 Python 标准库）。
- `config.json`：检索式、相关性关键词、标签分类、回溯天数，可直接修改。
- `site/index.html`：展示页，支持搜索、按时间/来源/标签筛选、排序，当天新增标记 NEW。
- `.github/workflows/research-tracker.yml`：每天 UTC 00:17（北京时间 08:17）运行抓取 → 提交数据 → 部署到 GitHub Pages。

## 启用

1. 合并到 `main`。
2. 仓库 Settings → Pages → Source 选 **GitHub Actions**。
3. Actions 页手动运行一次 “AI+CAD research tracker”，之后每天自动更新。

## 本地运行

```bash
python research-tracker/scraper.py            # 抓取并更新数据
python research-tracker/scraper.py --dry-run  # 只打印结果
cd research-tracker/site && python -m http.server  # 打开 http://localhost:8000
```
