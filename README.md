# FinanceAnalysis - Bilingual Financial Data SaaS Platform

A production-ready Django-based SaaS platform for analyzing Chinese financial markets with bilingual (English/Chinese) support, real-time data ingestion, technical analysis, and RESTful API.

## 🚀 Project Overview

This platform provides comprehensive financial data analysis for Chinese stock markets (CSI 300 Index), featuring:

- **Real-time Data Ingestion**: Automated daily synchronization of stock data using AkShare
- **Technical Analysis**: RSI, MACD, and extensible indicator calculations using TA-Lib
- **RESTful API**: Secure, rate-limited API with JWT authentication
- **Bilingual Support**: Full English/Chinese translation support
- **SaaS-Ready**: Multi-tier rate limiting and caching for scalability
- **Production Infrastructure**: Fully containerized with Docker Compose

## 🛠️ Technology Stack

### Backend
- **Django 6.0.1** - Web framework
- **Django REST Framework 3.15.1** - API framework
- **PostgreSQL 15** - Primary database
- **Redis 7** - Caching and message broker
- **Celery 5.4.0** - Asynchronous task processing
- **Celery Beat** - Periodic task scheduler

### Data & Analysis
- **AkShare** - Chinese financial data provider
- **Pandas 2.2.2** - Data manipulation
- **TA-Lib** - Technical analysis library
- **NumPy** - Numerical computing

### Infrastructure
- **Docker & Docker Compose** - Containerization
- **Nginx** - Reverse proxy (production-ready)

### Additional Tools
- **django-modeltranslation 0.18.12** - Model field translation
- **django-filter 24.2** - Advanced API filtering
- **djangorestframework-simplejwt 5.3.1** - JWT authentication

## ✅ Completed Phases

### Phase 1: Foundation & Docker Setup ✓
**Objective**: Production-ready project structure with containerization

**Achievements**:
- Split settings architecture (`base.py`, `local.py`, `production.py`)
- Django project initialized with custom apps structure
- Docker Compose configuration:
  - Django application service
  - PostgreSQL 15 database
  - Redis for caching and Celery broker
  - Celery worker and beat services
- Environment variable management with `django-environ`
- Static files handling

**Key Files**:
- `config/settings/base.py` - Core Django settings
- `docker-compose.yml` - Service orchestration
- `compose/local/django/Dockerfile` - Django container build
- `compose/local/django/start*.sh` - Service startup scripts

---

### Phase 2: Bilingual Data Modeling ✓
**Objective**: Create core financial data models with translation support

**Achievements**:
- **Market Model**: Stock exchanges (SSE, SZSE, BSE)
- **Asset Model**: Individual stocks with bilingual names
- **OHLCV Model**: Daily price data (Open, High, Low, Close, Volume)
- Integrated `django-modeltranslation` for field-level translations
- Django Admin interface with translation support
- Database migrations applied successfully

**Data Model**:
```
Market (3 exchanges)
  ├── Asset (334 CSI 300 stocks)
      └── OHLCV (Historical daily data)
```

**Key Files**:
- `apps/markets/models.py` - Core financial models
- `apps/markets/translation.py` - Translation configuration
- `apps/markets/admin.py` - Admin interface

---

### Phase 3: Data Ingestion Engine ✓
**Objective**: Automated data synchronization with AkShare

**Achievements**:
- Celery worker and beat services configured
- Distributed task architecture (dispatcher + workers)
- **Main Tasks**:
  - `sync_daily_a_shares()` - Dispatcher task for CSI 300 stocks
  - `sync_asset_history()` - Individual stock data processor
- Successfully imported 334 CSI 300 stocks with historical data
- Idempotent data handling (no duplicates on re-runs)
- Error handling and logging

**Data Coverage**:
- **Markets**: Shanghai (SSE), Shenzhen (SZSE), Beijing (BSE)
- **Stocks**: 334 CSI 300 constituents
- **Historical Data**: ~1 year of daily OHLCV data per stock

**Key Files**:
- `apps/markets/tasks.py` - Data ingestion tasks
- `config/celery.py` - Celery configuration
- `compose/local/django/start-celeryworker` - Worker startup script
- `compose/local/django/start-celerybeat` - Scheduler startup script

---

### Phase 4: Financial Analysis & Indicators ✓
**Objective**: Calculate technical indicators using TA-Lib

**Achievements**:
- TA-Lib C library compiled and installed in Docker container
- **TechnicalIndicator Model**: Stores calculated indicators with timestamps
- **Calculation Tasks**:
  - `calculate_rsi_for_asset()` - 14-period RSI calculation
  - `calculate_macd_for_asset()` - MACD calculation (12, 26, 9)
  - `calculate_indicators_for_all_assets()` - Batch processing dispatcher
- Pandas-based data pipeline for efficient computation
- Successfully calculated 668 indicators (334 RSI + 334 MACD)

**Indicators Calculated**:
- **RSI (Relative Strength Index)**: 14-period, identifies overbought/oversold conditions
- **MACD (Moving Average Convergence Divergence)**: 12/26/9 periods, trend indicator

**Key Files**:
- `apps/analytics/models.py` - TechnicalIndicator model
- `apps/analytics/tasks.py` - Indicator calculation tasks
- `compose/local/django/Dockerfile` - TA-Lib installation

---

### Phase 5: REST API with Caching ✓
**Objective**: Expose data through secure, performant API

**Achievements**:
- **API Endpoints**:
  - `/api/v1/markets/` - List markets
  - `/api/v1/assets/` - Search and filter stocks
  - `/api/v1/ohlcv/` - Historical price data
  - `/api/v1/indicators/` - Technical indicators
  - `/api/v1/indicators/top_rsi/` - Top 20 overbought stocks
  - `/api/v1/indicators/bottom_rsi/` - Top 20 oversold stocks
- **Features**:
  - Pagination (50 items per page)
  - Advanced filtering with `django-filter`
  - Search functionality
  - Redis caching (2 hours for data, 5 minutes for dynamic endpoints)
  - Optimized queries with `select_related()`
  - Separate list/detail serializers for performance
- **Browsable API** for easy testing and documentation

**API Capabilities**:
- Filter assets by market: `?market__code=SSE`
- Search by name/symbol: `?search=平安`
- Date range filtering for OHLCV and indicators
- Custom aggregations (top/bottom RSI)

**Key Files**:
- `apps/markets/serializers.py` - Market/Asset/OHLCV serializers
- `apps/markets/views.py` - Market API ViewSets
- `apps/analytics/serializers.py` - Indicator serializers
- `apps/analytics/views.py` - Analytics API ViewSets
- `config/urls.py` - API routing

---

### Phase 6: Production Readiness & SaaS Features ✓
**Objective**: Authentication, authorization, and rate limiting

**Achievements**:
- **JWT Authentication**:
  - SimpleJWT integration
  - Access tokens (60-minute lifetime)
  - Refresh tokens (7-day lifetime with rotation)
  - Token blacklist for security
- **Authentication Endpoints**:
  - `POST /api/v1/auth/token/` - Obtain tokens
  - `POST /api/v1/auth/token/refresh/` - Refresh access token
  - `POST /api/v1/auth/token/verify/` - Verify token validity
- **Rate Limiting (SaaS Tiers)**:
  - Anonymous users: 100 requests/day
  - Authenticated users: 1,000 requests/day
  - Premium users: 10,000 requests/day (extensible)
- **Security**:
  - Redis-backed caching
  - Stateless authentication
  - Token rotation and blacklisting
  - Custom throttle classes for tier management

**API Security Model**:
- Read-only access for unauthenticated users
- Full access for authenticated users
- Tier-based rate limiting
- JWT bearer token authentication

**Key Files**:
- `config/settings/base.py` - JWT and throttling configuration
- `apps/core/throttling.py` - Custom throttle classes
- `config/urls.py` - Authentication endpoints

---

### Phase 9: Stock Screeners & Alerts ✓
**Objective**: Automated screening and notification system

**Implemented Features**:
- Pre-built screener endpoints:
  - Overbought/Oversold stocks
  - Breakout candidates
  - High volume stocks
  - Trend reversal signals
- Saved screener templates API
- Alert rule management API (price + indicator conditions)
- Alert event history API
- Multi-channel notifications:
  - Email
  - SMS via webhook provider integration hook
  - WebSocket push notifications
- Celery periodic alert checks with configurable cooldown

**Technical Implementation**:
- `AlertRule`, `AlertEvent`, `ScreenerTemplate` models
- Celery tasks: `check_alert_rules`, `send_alert_notifications`
- WebSocket endpoint: `/ws/alerts/` (Channels + Redis channel layer)
- Router endpoints:
  - `/api/v1/screeners/`
  - `/api/v1/screener-templates/`
  - `/api/v1/alerts/`
  - `/api/v1/alert-events/`

**Key Files**:
- `apps/analytics/models.py` - Phase 9 data models
- `apps/analytics/tasks.py` - Alert evaluation and dispatch tasks
- `apps/analytics/views.py` - Screener and alert APIs
- `apps/analytics/consumers.py` - WebSocket alert consumer
- `config/asgi.py` - ASGI protocol routing for HTTP + WebSocket
- `config/settings/base.py` - Channels and periodic schedule config

---

## 📊 Current System Status

### Data Metrics
- **Markets**: 3 (SSE, SZSE, BSE)
- **Assets**: 334 CSI 300 stocks
- **OHLCV Records**: ~100,000+ daily price points
- **Technical Indicators**: 668 (334 RSI + 334 MACD)

### API Endpoints
- **Markets API**: 2 endpoints (list, detail)
- **Assets API**: 2 endpoints + search/filter
- **OHLCV API**: 2 endpoints + date range filtering
- **Indicators API**: 4 endpoints (list, detail, top_rsi, bottom_rsi)
- **Authentication**: 3 endpoints (token, refresh, verify)

### Performance
- Redis caching enabled (2-hour cache for static data)
- Database query optimization with `select_related()`
- Celery distributed task processing
- Docker containerization for scalability

---

## 🔮 Future Phases & Roadmap

### Phase 7: User Management & Subscriptions
**Objective**: Multi-tenant user system with subscription tiers

**Planned Features**:
- User registration and profile management
- Subscription tier model (Pro, Premium)
- Payment integration (Stripe)
- User dashboard with usage analytics
- Admin panel for user management
- Email verification and password reset

**Technical Implementation**:
- Extend Django User model with subscription fields
- Create `Subscription` and `UserProfile` models
- Implement middleware for tier enforcement
- Add webhook handlers for payment processing

---

### Phase 8: Advanced Technical Indicators
**Objective**: Expand analytical capabilities

**Planned Indicators**:
- **Bollinger Bands**: Volatility indicator
- **Moving Averages**: SMA, EMA (multiple periods)
- **Stochastic Oscillator**: Momentum indicator
- **ADX (Average Directional Index)**: Trend strength
- **OBV (On-Balance Volume)**: Volume-price indicator
- **Fibonacci Retracement**: Support/resistance levels

**Features**:
- Configurable indicator parameters
- Historical indicator values (time series)
- Indicator comparison charts
- Custom indicator combinations

---

### Phase 9: Stock Screeners & Alerts (Implemented)
**Objective**: Automated screening and notification system

**Screener Features**:
- Pre-built screeners:
  - Overbought/Oversold stocks
  - Breakout candidates
  - High volume stocks
  - Trend reversal signals
- Custom screener builder
- Saved screener templates
- Real-time screening results

**Alert System**:
- Price alerts (above/below threshold)
- Indicator alerts (RSI > 70, MACD crossover)
- Custom condition alerts
- Multi-channel notifications (Email, SMS, WebSocket)
- Alert history and management

**Technical Implementation**:
- WebSocket integration for real-time updates
- Celery periodic tasks for alert checking
- Email/SMS provider integration
- Alert state management in Redis

---

### Phase 10: Advanced Technical Indicators Expansion
**Objective**: 扩展技术指标体系，覆盖趋势、动量、波动率和量价信号

**均线系统**:
- MA5 / MA10 / MA20 / MA60 四周期均线
- 金叉 / 死叉信号自动检测与记录
- 均线多头排列 / 空头排列判断

**布林带 (Bollinger Bands)**:
- 波动率收缩（Squeeze）/ 扩张判断
- 价格突破上下轨信号
- 结合 RSI 的综合超买超卖判断

**量价关系模型**:
- OBV（On-Balance Volume）计算
- 成交量异动检测（相对 N 日均量的倍数阈值）
- 量价背离信号

**动量因子**:
- 过去 5 / 10 / 20 日涨幅动量
- 相对强弱（RS Score）排名
- 动量衰减检测

**反转因子**:
- 过度超跌检测（RSI 低位 + 布林带下轨 + 成交量萎缩组合）
- 底部反弹概率统计（基于历史相似形态）

**技术实现**:
- 扩展 `apps/analytics/tasks.py` 中的指标计算任务
- 新增 `SignalEvent` 模型记录信号触发时间和类型
- API 增加 `/api/v1/signals/` 端点
- Celery Beat 每日收盘后批量计算全部指标

**Key Files**:
- `apps/analytics/models.py` - 新增 SignalEvent 模型
- `apps/analytics/tasks.py` - 扩展指标计算
- `apps/analytics/indicators/` - 各指标独立模块

---

### Phase 11: Multi-Factor Alpha Model
**Objective**: 构建多因子选股模型，从沪深300中筛选底部个股候选

**财务因子**:
- PE 分位数（当前 PE 在历史 N 年中的百分位）
- PB 分位数
- ROE 趋势（近 4 季度 ROE 变化方向）
- 数据来源：AkShare 财务报表接口

**资金流向因子**:
- 北向资金（沪深港通）净流入/流出（近 5/10/20 日）
- 主力资金净流入（大单买入 - 大单卖出）
- A 股融资余额变化（融资买入额趋势）
- 数据来源：AkShare 资金流向接口

**综合评分模型**:
- 各因子标准化（Z-Score 归一化）
- 可配置权重的加权综合得分
- 输出：沪深300中综合得分排名前 N 的个股列表
- 支持按因子类别单独筛选（纯技术 / 纯基本面 / 综合）

**底部候选筛选逻辑**:
- 技术面：RSI < 35 + 布林带下轨附近 + 反转因子触发
- 基本面：PE/PB 历史低分位 + ROE 未恶化
- 资金面：北向或主力近期有净流入迹象
- 三类条件权重可调，输出每只股票的底部概率得分

**技术实现**:
- 新增 `apps/factors/` 应用
- `FactorScore` 模型记录每日因子得分快照
- API 增加 `/api/v1/screener/bottom-candidates/` 端点
- 支持参数化查询（权重、阈值、输出数量）

---

### Phase 12: Macro & Event-Driven Context Engine
**Objective**: 引入宏观背景变量和事件驱动分析，作为所有模型的全局调节层

**宏观因子数据接入**:
- 美元指数（DXY）
- 人民币兑美元汇率（CNY/USD）
- 中国10年期国债收益率
- PMI（制造业 / 非制造业）
- CPI / PPI 月度数据
- 数据来源：AkShare 宏观经济接口

**经济周期识别**:
- 基于 PMI + 国债收益率斜率构建简化版「美林时钟」
- 输出当前周期阶段：复苏 / 过热 / 滞胀 / 衰退
- 各周期下不同板块的历史超额收益统计

**事件驱动分析**:
- 事件库：记录重大历史事件（战争、贸易摩擦、重大政策出台）
- 事件影响统计：事件发生后 N 日各板块的平均涨跌幅
- 当前环境标签系统（支持手动打标）：如「中美贸易摩擦期」、「降息周期」
- 环境标签动态调整选股模型的因子权重

**全局背景变量注入**:
- 所有预测模型可接收「背景上下文」参数
- 背景变量影响因子权重（例：衰退周期时防御性因子权重上升）
- API 支持传入环境参数：`?macro_context=recession&event_tag=trade_war`

**技术实现**:
- 新增 `apps/macro/` 应用
- `MacroSnapshot` 模型记录每日宏观指标快照
- `MarketContext` 模型管理当前环境标签
- `EventImpactStat` 模型存储事件历史影响统计
- Celery Beat 每月同步宏观数据

---

### Phase 13: NLP Sentiment & News Intelligence
**Objective**: 中文财经新闻情绪分析，捕捉市场情绪信号

**数据来源**:
- 财经媒体：东方财富、同花顺、新浪财经（AkShare 新闻接口）
- 上市公司公告（交易所公告接口）
- 财报文本（季报/年报摘要）

**NLP 情绪分析**:
- 中文分词：jieba
- 预训练模型：FinBERT-Chinese 或 ERNIE-Finance（金融领域微调版）
- 输出：正面 / 中性 / 负面 情绪得分（0-1 区间）
- 个股新闻情绪聚合：近7日情绪均值 + 趋势方向

**概念板块热度**:
- 龙虎榜数据接入（AkShare）
- 涨停板统计：连板天数、涨停原因分类
- 板块热度评分：近 N 日涨停数量 + 资金净流入
- 热门概念自动标签（如「AI算力」、「新能源」）

**情绪信号整合**:
- 情绪得分作为因子加入多因子模型（Phase 11）
- 极端负面情绪 + 超跌 = 潜在反转信号
- 情绪骤变预警（单日情绪得分变化超过设定阈值）

**技术实现**:
- 新增 `apps/sentiment/` 应用
- `NewsArticle` + `SentimentScore` 模型
- Celery Beat 每日抓取最新新闻并计算情绪
- API 增加 `/api/v1/sentiment/` 端点

---

### Phase 14: ML Prediction Engine
**Objective**: 核心预测引擎——给出个股未来走势的方向概率

**预测目标**:
- 方向分类：3日 / 7日 / 30日后 涨 / 跌 / 横盘（三分类）
- 输出格式：`{"up": 0.45, "flat": 0.30, "down": 0.25, "confidence": 0.72}`

**模型一：XGBoost / LightGBM 分类模型**:
- 特征：技术指标（Phase 10）+ 多因子得分（Phase 11）+ 宏观因子（Phase 12）+ 情绪分（Phase 13）
- 标签：未来 N 日收益率分三档
- 优点：可解释性强，训练快，支持特征重要性可视化
- 用于：日常快速推断，特征选择验证

**模型二：LSTM 时序预测模型**:
- 输入：过去60日的 OHLCV + 技术指标序列
- 架构：双层 LSTM + Dropout + Softmax 输出
- 框架：PyTorch
- 用于：捕捉价格序列中的时序依赖模式

**模型集成**:
- XGBoost + LSTM 加权集成（Ensemble）
- 集成权重基于滚动历史预测准确率动态调整
- 最终输出：集成概率 + 置信度区间

**模型训练与更新**:
- 训练数据：沪深300历史数据（5年+）
- 滚动窗口训练：每月用最新数据重新训练
- 模型版本管理：MLflow 或文件版本控制
- Celery 任务：每周末自动触发模型更新

**API 输出**:
- `/api/v1/prediction/{stock_code}/` — 单股预测
- `/api/v1/prediction/batch/` — 批量预测（支持全沪深300）
- 支持传入宏观背景参数（对接 Phase 12 环境标签）

**技术实现**:
- 新增 `apps/prediction/` 应用
- `PredictionResult` 模型存储每日预测快照
- `ModelVersion` 模型管理训练版本
- 依赖：`lightgbm`, `torch`, `scikit-learn`, `mlflow`

---

### Phase 15: Backtesting & Strategy Validation
**Objective**: 验证预测模型的实际有效性，量化策略回测

**回测引擎**:
- 基于历史数据模拟策略执行
- 支持：单股回测 / 投资组合回测
- 手续费、滑点、涨跌停约束模拟（A股特有）
- 风险指标：年化收益、最大回撤、Sharpe 比率、胜率

**策略模板**:
- 底部候选买入策略（对接 Phase 11 筛选器）
- 预测概率阈值策略（对接 Phase 14 预测引擎）
- 宏观周期轮动策略（对接 Phase 12 周期判断）

**可视化报告**:
- 回测净值曲线
- 持仓明细与买卖点标注
- 因子暴露分析

**技术实现**:
- 新增 `apps/backtest/` 应用
- `BacktestRun` + `BacktestTrade` 模型
- API 增加 `/api/v1/backtest/` 端点（异步任务触发）
- 报告导出：JSON / CSV / PDF

---

### Phase 16: Frontend Dashboard
**Objective**: 面向用户的可视化操作界面

**核心页面**:
- **首页仪表盘**：当前宏观环境标签、板块热度、今日预测信号汇总
- **个股详情页**：K线图 + 技术指标 + 预测概率 + 情绪趋势
- **底部候选筛选器**：可配置权重的多因子筛选结果列表
- **宏观背景设置**：手动打标当前环境（周期、事件标签）
- **回测工作台**：策略参数配置 + 回测结果展示
- **告警中心**：价格/信号告警管理（对接 Phase 9）

**技术栈**:
- **React 18** + TypeScript
- **TradingView Lightweight Charts** — K线图
- **Recharts / ECharts** — 因子得分、概率可视化
- **WebSocket** — 实时价格更新
- **Tailwind CSS** + shadcn/ui
- **Vite** — 构建工具

---

### Phase 17: Mobile Application
**Objective**: iOS / Android 移动端应用

**核心功能**:
- 个股搜索与收藏
- 实时价格跟踪 + 预测概率查看
- 底部候选推送通知
- 移动端优化图表
- 离线数据缓存

**技术选型**:
- **React Native** — 跨平台首选（复用前端逻辑）
- 或 **Flutter** — 更高性能需求时备选

---

### Phase 18: Production Deployment
**Objective**: 云端部署 + CI/CD 全自动化

**基础设施**:
- 云服务商：AWS / 阿里云（国内用户推荐阿里云）
- 容器编排：Kubernetes (EKS/ACK) 或 Docker Compose（小规模）
- 托管数据库：RDS PostgreSQL
- 托管缓存：ElastiCache / Redis 企业版
- CDN：CloudFront / 阿里云 CDN
- 对象存储：S3 / OSS（模型文件、报告导出）

**DevOps**:
- CI/CD：GitHub Actions
- 蓝绿部署 / 滚动更新
- 监控：Prometheus + Grafana
- 错误追踪：Sentry
- 日志：ELK Stack 或阿里云日志服务
- SSL 证书自动续签

---

### Phase 19: API Documentation & Developer Portal
**Objective**: 完整的 API 文档和开发者生态

**功能**:
- OpenAPI / Swagger 自动生成文档
- 交互式 API 测试界面
- 代码示例（Python / JavaScript / cURL）
- API Key 管理
- 请求量统计与限流监控
- 开发者沙箱环境
- API 变更日志与版本管理

---

## 🚦 Getting Started

### Prerequisites
- Docker and Docker Compose
- Git

### Installation

1. **Clone the repository**:
   ```bash
   git clone https://github.com/sean7084/FinanceAnalysis.git
   cd FinanceAnalysis
   ```

2. **Set up environment variables**:
   ```bash
   # Create .envs/.local file with:
   DATABASE_URL=postgres://postgres:postgres@postgres:5432/finance_analysis
   DJANGO_SECRET_KEY=your-secret-key-here
   DJANGO_READ_DOT_ENV_FILE=True
   DJANGO_DEBUG=True
   ```

3. **Build and start services**:
   ```bash
   docker-compose up --build -d
   ```

4. **Run migrations**:
   ```bash
   docker-compose exec django python manage.py migrate
   ```

5. **Create superuser**:
   ```bash
   docker-compose exec django python manage.py createsuperuser
   ```

6. **Import CSI 300 data**:
   ```bash
   docker-compose exec django python manage.py shell
   >>> from apps.markets.tasks import sync_daily_a_shares
   >>> sync_daily_a_shares.delay()
   >>> exit()
   ```

7. **Calculate indicators**:
   ```bash
   docker-compose exec django python manage.py shell
   >>> from apps.analytics.tasks import calculate_indicators_for_all_assets
   >>> calculate_indicators_for_all_assets.delay()
   >>> exit()
   ```

8. **Access the application**:
   - Admin: `http://localhost:8000/admin/`
   - API: `http://localhost:8000/api/v1/`

---

## 📡 API Usage Examples

### Obtain JWT Token
```bash
curl -X POST http://localhost:8000/api/v1/auth/token/ \
  -H "Content-Type: application/json" \
  -d '{"username": "your_username", "password": "your_password"}'
```

### List Assets (Authenticated)
```bash
curl http://localhost:8000/api/v1/assets/ \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN"
```

### Get Top RSI Stocks
```bash
curl http://localhost:8000/api/v1/indicators/top_rsi/ \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN"
```

### Search Stocks
```bash
curl "http://localhost:8000/api/v1/assets/?search=平安" \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN"
```

---

## 📁 Project Structure

```
FinanceAnalysis/
├── apps/
│   ├── analytics/          # Technical indicators
│   │   ├── models.py       # TechnicalIndicator model
│   │   ├── tasks.py        # Indicator calculations
│   │   ├── views.py        # Analytics API
│   │   └── serializers.py  # Indicator serializers
│   ├── core/               # Shared utilities
│   │   └── throttling.py   # Custom rate limiting
│   └── markets/            # Market data
│       ├── models.py       # Market, Asset, OHLCV models
│       ├── tasks.py        # Data ingestion
│       ├── views.py        # Market API
│       └── serializers.py  # Market serializers
├── compose/
│   └── local/
│       ├── django/         # Django Docker config
│       └── nginx/          # Nginx config (production)
├── config/
│   ├── settings/
│   │   ├── base.py        # Core settings
│   │   ├── local.py       # Development settings
│   │   └── production.py  # Production settings
│   ├── celery.py          # Celery configuration
│   └── urls.py            # URL routing
├── requirements/
│   ├── base.txt           # Core dependencies
│   ├── local.txt          # Development dependencies
│   └── production.txt     # Production dependencies
├── docker-compose.yml      # Service orchestration
└── manage.py              # Django management script
```

---

## 🤝 Contributing

This is a personal project, but contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Submit a pull request

---

## 📄 License

This project is private and proprietary.

---

## 👤 Author

**Sean Liu**
- GitHub: [@sean7084](https://github.com/sean7084)

---

## 🙏 Acknowledgments

- **AkShare** - Chinese financial data provider
- **TA-Lib** - Technical analysis library
- **Django & DRF** - Web framework and API tools
- **Celery** - Distributed task queue

---

**Last Updated**: January 9, 2026  
**Version**: 1.0.0 (Phases 1-6 Complete)
