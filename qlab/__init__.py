"""QLab —— A 股多因子量化交易系统。

模块分层（详见 README）：
    qlab.data      数据层：行情抓取、本地缓存、面板装配
    qlab.factors   计算层：因子库、预处理、IC 分析
    qlab.strategy  策略层：因子合成、择时、目标权重
    qlab.backtest  回测层：撮合、组合记账、交易成本
    qlab.analytics 指标层：绩效指标、归因、报表导出
    qlab.viz       展示层：Pillow 绘图（净值/回撤/热力图）
    qlab.webapp    展示层：标准库 HTTP 服务 + 前端看板
"""

__version__ = "1.0.0"
__all__ = ["__version__"]
