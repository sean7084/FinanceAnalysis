# Universe campaign evidence log (CSI500 → CSI1000)

> 权威设计：`~/.qoder-cn/plans/hushed-gorge-moorhen.md`。**两者冲突时，以计划文件为准。**
> 本文件只放证据（日期 + 测量值 + 命令输出摘要），append-only；更正写进计划正文。

## 2026-11-20 — W0-1 复测 + G 门评估

命令：`bash tmp/w0_verify.sh`（WSL，repo 6113710 = origin/main，工作区干净）。

- CI 地板：`.github/workflows/ci.yml:263 MIN_TEST_COUNT: "1316"`；frontend `:486 "100"`。
- 迁移：`showmigrations analytics markets prediction backtest` → **ALL APPLIED**（无 `[ ]`）。
- Active 股票 fleet（`LightGBMModelArtifact.objects.filter(is_active=True)`，metrics_json）：
  - 3d `lgb-3d-2026-11-09` purged_holdout rank_ic **0.004109426439005** / t **0.36384272515990107** / icir **0.023011434431758772**
  - 7d `lgb-7d-2026-11-09` purged_holdout rank_ic **0.023425059800029336** / t **1.9679510583777697** / icir **0.12446415336425479**
  - 30d `lgb-30d-2026-11-09` purged_holdout rank_ic **0.055947430422276546** / t **3.9600240474305006** / icir **0.2504539115783808**
  - ⇒ 计划 v1 的 "icir≈0.23" 更正为 **0.250**；7d t=1.968 边界噪声（非反向）；30d icir 0.250 < 0.3 仍不合格一条。
- 足迹：`pg_database_size` = **59 GB**（= 54.9 GiB ≤ 66.23 目标；CHANGELOG campaign B 记录一致）。
- Stack：`verify_local_stack.sh` → PostgreSQL ready（192.168.31.8:5432/finance_analysis）、Redis broker/cache ready、deps ok、0 system check issues、migrations all applied。
- 进程：runserver ×2 + celery workers（ops/backtest/train-lightgbm,train-lstm/etf-rotation）+ **beat（DatabaseScheduler）在跑**（10:14 起）；tmux 无会话。
- GitHub：无开放 PR；开放 issues 含审计新立 **#183**（yc_cb P1）/ **#184**（env.md inert）/ **#185**（BACKLOG 索引矛盾）/ **#186**（DQ docstring）/ **#187**（sentiment 残余）/ **#189**（ETF 9-cell grid run 跟踪）。
- 存储 campaign：PR **#188** 已合并（b16a1ed）——param_key 唯一性 + SMA-60 + DQ variant key；CHANGELOG 记录 remaining_null=0、dup 0 groups、0016–0018 applied、VACUUM FULL 48 min、132.46→51.2 GiB（B 后 54.9）、速度偏差诚实记录、全套件 1316 OK、地板 1306→1316。
- G 门：G1–G6 ✅（上条证据）；G7 WSL clone ✅ / Windows clone 待数据运行前核；G8 stack ✅ / 全套件后台跑（`tmp/w0_baseline_suite.log`）。

待补：基线套件结果；Windows clone 状态。