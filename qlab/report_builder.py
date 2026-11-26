"""生成 Word 技术报告（课程交付物之一）。

报告结构对应作业要求：
    1. 摘要与结论
    2. 数据来源与数据工程（含质量检查）
    3. 系统架构与模块划分（含架构图）
    4. 策略设计：因子、信号、组合构建、择时、交易成本
    5. 回测设置与结果（指标表、净值图、回撤图、月度热力图、因子IC）
    6. 样本内 / 样本外对比与超额收益来源
    7. 参数敏感性与消融实验
    8. 已知局限与改进方向
    9. AI 辅助说明与复现步骤
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Iterable

import pandas as pd
from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from qlab.config import (
    FIGURE_DIR,
    PROJECT_ROOT,
    REPORT_DIR,
    TABLE_DIR,
)
from qlab.logging_utils import get_logger

log = get_logger("qlab.docx")

PRIMARY = RGBColor(0x1F, 0x4E, 0x79)
MUTED = RGBColor(0x60, 0x6B, 0x7A)


# ----------------------------------------------------------------------
# 样式工具
# ----------------------------------------------------------------------
def _set_cn_font(run, name: str = "微软雅黑", size: float | None = None,
                 bold: bool | None = None, color: RGBColor | None = None):
    run.font.name = name
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = rpr.makeelement(qn("w:rFonts"), {})
        rpr.append(rfonts)
    rfonts.set(qn("w:eastAsia"), name)
    rfonts.set(qn("w:ascii"), name)
    rfonts.set(qn("w:hAnsi"), name)
    if size is not None:
        run.font.size = Pt(size)
    if bold is not None:
        run.font.bold = bold
    if color is not None:
        run.font.color.rgb = color


def _para(doc, text: str = "", size: float = 10.5, bold: bool = False,
          align=None, space_after: float = 6, color: RGBColor | None = None,
          indent: float = 0):
    p = doc.add_paragraph()
    if align is not None:
        p.alignment = align
    p.paragraph_format.space_after = Pt(space_after)
    p.paragraph_format.line_spacing = 1.35
    if indent:
        p.paragraph_format.first_line_indent = Cm(indent)
    if text:
        run = p.add_run(text)
        _set_cn_font(run, size=size, bold=bold, color=color)
    return p


def _heading(doc, text: str, level: int = 1):
    sizes = {1: 16, 2: 13, 3: 11.5}
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(14 if level == 1 else 10)
    p.paragraph_format.space_after = Pt(6)
    run = p.add_run(text)
    _set_cn_font(run, size=sizes.get(level, 11), bold=True,
                 color=PRIMARY if level <= 2 else RGBColor(0x33, 0x33, 0x33))
    return p


def _bullet(doc, text: str, size: float = 10.5):
    p = doc.add_paragraph(style="List Bullet")
    p.paragraph_format.space_after = Pt(3)
    p.paragraph_format.line_spacing = 1.3
    run = p.add_run(text)
    _set_cn_font(run, size=size)
    return p


def _table(doc, df: pd.DataFrame, widths: list[float] | None = None,
           font_size: float = 8.5, header_fill: str = "1F4E79",
           index: bool = False, first_col_bold: bool = False):
    """插入表格（带表头底色、右对齐数值）。"""
    data = df.reset_index() if index else df
    n_rows, n_cols = data.shape[0] + 1, data.shape[1]
    table = doc.add_table(rows=n_rows, cols=n_cols)
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER

    for j, col in enumerate(data.columns):
        cell = table.cell(0, j)
        cell.text = ""
        p = cell.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = p.add_run(str(col))
        _set_cn_font(run, size=font_size, bold=True, color=RGBColor(0xFF, 0xFF, 0xFF))
        shading = cell._tc.get_or_add_tcPr().makeelement(
            qn("w:shd"), {qn("w:fill"): header_fill, qn("w:val"): "clear"}
        )
        cell._tc.get_or_add_tcPr().append(shading)

    for i in range(data.shape[0]):
        for j, col in enumerate(data.columns):
            val = data.iloc[i, j]
            cell = table.cell(i + 1, j)
            cell.text = ""
            p = cell.paragraphs[0]
            text = _fmt_cell(val, col)
            is_num = isinstance(val, (int, float)) and not isinstance(val, bool)
            p.alignment = WD_ALIGN_PARAGRAPH.RIGHT if is_num else WD_ALIGN_PARAGRAPH.CENTER
            run = p.add_run(text)
            _set_cn_font(run, size=font_size,
                         bold=first_col_bold and j == 0)
    if widths:
        for j, w in enumerate(widths):
            for row in table.rows:
                row.cells[j].width = Cm(w)
    return table


def _fmt_cell(val, col) -> str:
    if isinstance(val, float):
        if pd.isna(val):
            return "—"
        if any(k in str(col) for k in ("比例", "率", "%", "收益", "回撤", "波动", "胜率", "换手", "捕获")):
            return f"{val * 100:.2f}%" if abs(val) < 10 else f"{val:.2f}"
        return f"{val:.3f}"
    return str(val)


def _figure(doc, path: Path, caption: str, width_cm: float = 15.5):
    if not Path(path).exists():
        log.warning("缺少图片 %s，跳过", path)
        return
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(6)
    p.add_run().add_picture(str(path), width=Cm(width_cm))
    cap = doc.add_paragraph()
    cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
    cap.paragraph_format.space_after = Pt(10)
    run = cap.add_run(caption)
    _set_cn_font(run, size=9, color=MUTED)


def _kv_table(doc, rows: Iterable[tuple[str, str]], font_size: float = 9):
    rows = list(rows)
    table = doc.add_table(rows=len(rows), cols=2)
    table.style = "Table Grid"
    for i, (k, v) in enumerate(rows):
        c0, c1 = table.cell(i, 0), table.cell(i, 1)
        c0.text, c1.text = "", ""
        r0 = c0.paragraphs[0].add_run(k)
        _set_cn_font(r0, size=font_size, bold=True)
        r1 = c1.paragraphs[0].add_run(v)
        _set_cn_font(r1, size=font_size)
        c0.width, c1.width = Cm(4.0), Cm(11.5)
    return table


# ----------------------------------------------------------------------
# 主构建函数
# ----------------------------------------------------------------------
def build_technical_report(output_dir: str | Path | None = None,
                           title: str | None = None) -> Path:
    out_dir = Path(output_dir) if output_dir else REPORT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    summary_path = TABLE_DIR / "result_summary.json"
    if not summary_path.exists():
        raise FileNotFoundError("缺少回测结果，请先运行 python -m qlab backtest")

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    metrics_df = pd.read_csv(TABLE_DIR / "metrics_by_segment.csv", index_col=0)
    factor_ic = pd.read_csv(TABLE_DIR / "factor_ic.csv")
    selected = pd.read_csv(TABLE_DIR / "factor_selected.csv")
    ablation_path = TABLE_DIR / "ablation.csv"
    ablation = pd.read_csv(ablation_path, index_col=0) if ablation_path.exists() else None

    def _ab(name: str, col: str, default: float = float("nan")) -> float:
        """读取消融实验表中的一个数值（表内百分比已按 % 存储）。"""
        if ablation is None or name not in ablation.index or col not in ablation.columns:
            return default
        return float(ablation.loc[name, col])
    quality_path = TABLE_DIR / "data_quality.csv"
    quality = pd.read_csv(quality_path) if quality_path.exists() else None

    meta = summary["meta"]
    bt_cfg = summary["config"]
    m_all = summary["metrics"].get("全样本", {})
    m_is = summary["metrics"].get("样本内", {})
    m_oos = summary["metrics"].get("样本外", {})

    # 策略参数（由 run_backtest.py 写入 meta，缺省时回退到默认值）
    top_n = int(meta.get("top_n", 30))
    timing_ma = int(meta.get("timing_ma", 120))
    timing_low = float(meta.get("timing_low", 0.30))

    # 单元测试数量从 tests/ 目录实时统计，避免文档里写死的数字过期
    n_tests = sum(
        len([
            node for node in ast.walk(ast.parse(p.read_text(encoding="utf-8")))
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
        ])
        for p in sorted((PROJECT_ROOT / "tests").glob("test_*.py"))
    )

    doc = Document()
    # 页面设置：A4 纵向，页边距 2.2cm
    section = doc.sections[0]
    section.page_width, section.page_height = Cm(21.0), Cm(29.7)
    section.left_margin = section.right_margin = Cm(2.2)
    section.top_margin = section.bottom_margin = Cm(2.2)
    style = doc.styles["Normal"]
    style.font.name = "微软雅黑"
    style.element.rPr.rFonts.set(qn("w:eastAsia"), "微软雅黑")

    # ---------------- 封面 ----------------
    _para(doc, "", size=10, space_after=60)
    _para(doc, title or "QLab：A 股多因子选股与趋势择时量化交易系统",
          size=22, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER, space_after=10)
    _para(doc, "项目技术报告", size=15, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER,
          space_after=8, color=PRIMARY)
    _para(doc, "课程：量化交易　|　形式：小组项目（4 人）", size=11,
          align=WD_ALIGN_PARAGRAPH.CENTER, space_after=4)
    _para(doc, "组别：L 组　|　组长：庄昊钊　|　组员：冯培智、庄昊钊、罗胜辉、甘洺宇", size=11,
          align=WD_ALIGN_PARAGRAPH.CENTER, space_after=4)
    _para(doc, f"回测区间：{bt_cfg['start']} ~ {bt_cfg['end']}", size=11,
          align=WD_ALIGN_PARAGRAPH.CENTER, space_after=4)
    _para(doc, f"股票池：{meta['universe_size']} 只 A 股（市值前 {meta['universe_requested']} 名）",
          size=11, align=WD_ALIGN_PARAGRAPH.CENTER, space_after=4)
    _para(doc, "代码仓库：GitHub（见提交邮件）　|　运行环境：Python 3.12",
          size=11, align=WD_ALIGN_PARAGRAPH.CENTER, space_after=4)
    doc.add_page_break()

    # ---------------- 1 摘要 ----------------
    _heading(doc, "一、摘要", 1)
    _para(doc,
          f"本项目实现了一个可真实运行的 A 股量化交易系统 QLab，覆盖数据接入、因子计算、"
          f"策略信号、回测内核、绩效归因与可视化展示六个环节。策略采用「多因子横截面打分选股 + "
          f"沪深 300 趋势择时」的两层结构：每月末用 {len(selected)} 个因子合成综合得分，"
          f"买入得分最高的 {top_n} 只股票等权持有；"
          f"同时用沪深 300 收盘价与 {timing_ma} 日均线的关系控制总仓位"
          f"（均线下方降至 {timing_low * 100:.0f}%）。",
          indent=0.74)
    _para(doc,
          f"在 {bt_cfg['start']} ~ {bt_cfg['end']} 的回测区间内，策略累计收益 "
          f"{m_all.get('累计收益率', 0) * 100:.1f}%、年化 {m_all.get('年化收益率', 0) * 100:.1f}%、"
          f"最大回撤 {m_all.get('最大回撤', 0) * 100:.1f}%、夏普 {m_all.get('夏普比率', 0):.2f}；"
          f"同期{meta['benchmark']}累计 {m_all.get('基准累计收益率', 0) * 100:.1f}%、"
          f"最大回撤 {m_all.get('基准最大回撤', 0) * 100:.1f}%、夏普 {m_all.get('基准夏普比率', 0):.2f}。"
          f"年化超额收益 {m_all.get('超额收益(年化)', 0) * 100:.1f} 个百分点，"
          f"信息比率 {m_all.get('信息比率', 0):.2f}。",
          indent=0.74)
    _para(doc,
          f"按样本内（2019-2022）/样本外（2023 至今）切分后，策略年化收益分别为 "
          f"{m_is.get('年化收益率', 0) * 100:.1f}% 与 {m_oos.get('年化收益率', 0) * 100:.1f}%，"
          f"夏普分别为 {m_is.get('夏普比率', 0):.2f} 与 {m_oos.get('夏普比率', 0):.2f}。"
          f"所有参数（因子选择、因子方向、择时窗口）只由样本内数据决定，样本外数据未参与任何选择。",
          indent=0.74)

    # ---------------- 2 数据 ----------------
    _heading(doc, "二、数据来源与数据工程", 1)
    _heading(doc, "2.1 数据来源", 2)
    _bullet(doc, "股票池：新浪财经行情中心 A 股列表接口，按抓取当日总市值降序取前 300 只，"
                 "代表沪深 300 的大市值样本；剔除 ST、退市标的，以及上市时间不足、"
                 "拿不到 2017-09 之前完整历史行情的标的。")
    _bullet(doc, "日线行情（主源）：akshare，免费、免注册，覆盖 A 股日线与指数。该库优先走东方财富"
                 "接口，对高频请求会临时限流，此时自动切换到新浪通道取数，单只标的失败不影响整批。"
                 f"数据区间 {meta.get('data_start', '')} ~ {meta.get('data_end', '')}，"
                 f"共 {meta.get('n_trading_days', '')} 个交易日；成交量统一换算为「手」，"
                 "价格统一为不复权。")
    _bullet(doc, "独立校验源：baostock，同样免注册。抽样若干标的，把本地缓存与 baostock 的收盘价逐日"
                 "比对，实测抽样的 8 只全部一致（最大绝对差 0.01 元，属低价股一分钱的取整），"
                 "用于确认主源数据没有错位或缺日。"
                 "复核命令：python -m qlab check --sample 15。"
                 "腾讯与新浪接口保留为历史兼容与回退。")
    _bullet(doc, "基准：沪深 300 指数（sh000300）日收盘价，与个股同源、同为不含分红的价格序列，口径一致。")
    _bullet(doc, "基准选择理由：本策略只买 A 股大市值股票，沪深 300 是同一域内最有代表性、"
                 "且最常被用作 A 股业绩基准的宽基指数；同时补充中证 500 作为风格参照，"
                 "用于说明策略收益并非单纯由大盘风格驱动。")

    _heading(doc, "2.2 复权口径的选择（关键工程决策）", 2)
    _para(doc,
          "本项目统一使用不复权（day）日线。三种口径各有代价：前复权序列会随每一次新分红整体"
          "重算，同一历史日期在不同时间取数得到的价格不同，回测时相当于让策略提前知道未来的分红，"
          "构成隐性前视；后复权价格随时间累积放大（贵州茅台 2026 年可达约 1 万元），"
          "按真实资金量下单时连一手都买不起，成交金额与滑点也失真；不复权则价格与真实市价一致，"
          "下单约束真实可执行。",
          indent=0.74)
    _para(doc,
          "不复权的代价是：个股除权除息日会形成价格跳空，绝对收益不含分红，A 股平均股息率约 2%，"
          "因此策略与基准的绝对收益都被低估约 2 个百分点/年。但本项目的基准是沪深 300 价格指数，"
          "同样不含分红，两者口径一致，超额收益的比较仍然公平。为控制除权跳空对因子的干扰，"
          "数据层另做了单日涨跌幅异常检查（|单日收益| > 25% 视为脏数据剔除）。",
          indent=0.74)

    _heading(doc, "2.3 数据质量检查", 2)
    _para(doc,
          "行情数据在进入因子计算前要过一道质量闸门：统计每只标的的样本行数、首末交易日、"
          "缺失比例与单日涨跌幅。A 股有涨跌停制度（主板 ±10%、双创 ±20%、ST ±5%），"
          "单日收益理论上不会超过 25%，超过该阈值的基本只有两种情况：数据源本身错乱，"
          "或者除权除息在不复权序列上留下的跳空。系统统一按异常处理并剔除该标的，"
          "体检结果写入 data_quality.csv，避免个别的错误序列把组合净值带偏。",
          indent=0.74)
    if quality is not None:
        n_ok = int(quality["ok"].sum()) if "ok" in quality else 0
        _para(doc, f"本次数据体检：{len(quality)} 只标的，合格 {n_ok} 只；"
                   f"其余因上市时间不足或极值异常被剔除。", indent=0.74)

    _heading(doc, "2.4 数据字典", 2)
    _table(doc, pd.DataFrame([
        {"字段": "date", "含义": "交易日", "示例": "2024-01-02"},
        {"字段": "open", "含义": "开盘价（不复权，等于真实市价）", "示例": "1715.00"},
        {"字段": "high / low", "含义": "当日最高 / 最低价", "示例": "1718.19 / 1678.10"},
        {"字段": "close", "含义": "收盘价", "示例": "1685.01"},
        {"字段": "volume", "含义": "成交量（手）", "示例": "32156"},
    ]), widths=[3.2, 7.0, 5.0])

    # ---------------- 3 架构 ----------------
    _heading(doc, "三、系统架构与模块划分", 1)
    _para(doc,
          "系统按「数据层 → 计算层 → 策略层 → 回测层 → 输出层」单向依赖组织，"
          "每层只依赖下一层的抽象接口，因此替换数据源或替换策略都不需要改动回测内核。"
          "核心包约 3700 行，含脚本与测试约 4400 行，模块划分与职责如下表。", indent=0.74)
    _figure(doc, FIGURE_DIR / "architecture.png", "图 1　QLab 系统架构与数据流", width_cm=16.0)

    _table(doc, pd.DataFrame([
        {"模块": "qlab.data", "职责": "数据接入：多数据源适配、本地缓存、质量检查、面板装配"},
        {"模块": "qlab.factors", "职责": "计算：12 个候选因子、去极值、标准化、IC/ICIR 分析"},
        {"模块": "qlab.strategy", "职责": "策略：样本内因子筛选、加权合成、Top-N 组合、择时"},
        {"模块": "qlab.backtest", "职责": "回测：t+1 开盘撮合、手续费/印花税/滑点、日频结算"},
        {"模块": "qlab.analytics", "职责": "输出：绩效指标、样本内外对比、Excel/Markdown 导出"},
        {"模块": "qlab.viz", "职责": "展示：Pillow 绘图（净值/回撤/热力图/因子 IC）"},
        {"模块": "qlab.webapp", "职责": "展示：标准库 HTTP 服务 + 前端看板，支持在线重跑回测"},
        {"模块": "tests/", "职责": f"{n_tests} 个单元测试：撮合时点、费用记账、指标公式、因子数学、权重约束"},
    ]), widths=[3.4, 12.0])

    _heading(doc, "3.1 回测内核的三个关键约定", 2)
    _bullet(doc, "信号与成交严格分离：t 日收盘后用当日及历史数据计算目标权重，t+1 日开盘价撮合。"
                 "单元测试用\"跳空 10 倍\"的构造行情验证了这一点——若误用收盘价成交，净值会放大 10 倍。")
    _bullet(doc, "成本按 A 股实际规则计：佣金万 2.5（单笔最低 5 元）、卖出印花税（2023-08-28 前千 1、"
                 "之后千 0.5）、单边滑点 5bp、按 100 股整手取整、现金不足时按比例缩减买单。")
    _bullet(doc, "停牌与退市处理：当日无开盘价的标的跳过调仓并保留持仓，估值沿用最近有效收盘价。")

    # ---------------- 4 策略 ----------------
    _heading(doc, "四、策略设计", 1)
    _heading(doc, "4.1 整体思路", 2)
    _para(doc,
          "策略对应课程中的「多因子选股」与「择时」两类方法。多因子部分解决\"买什么\"："
          "在横截面上用若干具有经济含义的因子给股票打分，买入综合得分最高的一篮子股票；"
          "择时部分解决\"买多少\"：用沪深 300 指数与 120 日均线的关系决定总仓位。"
          "两层结构的好处是：选股 alpha 与市场 beta 分离，回撤控制不依赖个股择时。", indent=0.74)

    _heading(doc, "4.2 候选因子与筛选", 2)
    _para(doc,
          "候选池共 12 个因子，覆盖动量、反转、波动率、流动性、趋势与高阶矩等类别。"
          "筛选规则分两步：(1) 按样本内 ICIR 绝对值降序；(2) 贪心加入，若与已选因子"
          "截面相关性超过 0.75 则跳过，避免同质因子重复计权。下表给出全部候选因子的"
          "样本内 IC 检验结果与最终权重。", indent=0.74)

    ic_show = factor_ic.copy()
    ic_show["入选"] = ic_show["factor"].isin(selected["factor"]).map({True: "✔", False: ""})
    ic_show = ic_show[["factor", "desc", "mean_ic", "icir", "t_stat", "入选"]].rename(
        columns={"factor": "因子", "desc": "含义", "mean_ic": "IC 均值",
                 "icir": "ICIR", "t_stat": "t 值"}
    )
    ic_show["IC 均值"] = ic_show["IC 均值"].map(lambda v: f"{v:+.4f}")
    ic_show["ICIR"] = ic_show["ICIR"].map(lambda v: f"{v:+.3f}")
    ic_show["t 值"] = ic_show["t 值"].map(lambda v: f"{v:+.2f}")
    _table(doc, ic_show, widths=[2.4, 6.0, 2.2, 1.8, 1.6, 1.4])
    _para(doc, "注：IC 为因子值与未来 21 个交易日收益的横截面 Spearman 秩相关，"
               "在样本内的月度调仓日上采样；ICIR = IC 均值 / IC 标准差。",
          size=8.5, color=MUTED)

    _heading(doc, "4.3 因子方向与加权", 2)
    sel_show = selected[["factor", "desc", "ic_mean", "icir", "sign", "weight"]].copy()
    sel_show["sign"] = sel_show["sign"].map({1: "正向", -1: "反向"})
    sel_show = sel_show.rename(columns={"factor": "因子", "desc": "含义", "ic_mean": "IC 均值",
                                        "icir": "ICIR", "sign": "方向", "weight": "权重"})
    sel_show["IC 均值"] = sel_show["IC 均值"].map(lambda v: f"{v:+.4f}")
    sel_show["ICIR"] = sel_show["ICIR"].map(lambda v: f"{v:+.3f}")
    sel_show["权重"] = sel_show["权重"].map(lambda v: f"{v:.3f}")
    _table(doc, sel_show, widths=[2.4, 6.2, 2.0, 1.7, 1.4, 1.4])
    _para(doc,
          "综合得分 = Σ 权重 × 方向 × 标准化因子值。方向取该因子样本内 IC 的符号："
          "IC 为正说明\"因子值越大、未来收益越高\"，权重取 |ICIR| 归一化。"
          "每个调仓日再做一次横截面 z-score（先按 2% 双侧缩尾），"
          "并对单日缺失的因子按有效权重重新归一，避免停牌或新股导致得分失真。", indent=0.74)

    _heading(doc, "4.4 组合构建与择时", 2)
    _bullet(doc, f"调仓频率：每月最后一个交易日生成信号，次一交易日开盘调仓；首个回测日额外建仓一次。")
    _bullet(doc, f"持仓数量：综合得分前 {top_n} 只，等权；单票权重上限 8%。")
    _bullet(doc, f"择时规则：沪深 300 收盘价 > {timing_ma} 日均线 → 满仓；否则仓位降至 "
                 f"{timing_low * 100:.0f}%。该规则只影响总仓位，不改变选股结果。")
    _bullet(doc, "样本隔离：因子选择、方向、权重与时序参数全部只用 2023-01-01 之前的数据确定"
                 "（2017-06 起，含因子滚动窗口的预热段）；2023 年之后的数据只用于样本外检验，"
                 "不参与任何决策。")

    # ---------------- 5 回测设置与结果 ----------------
    _heading(doc, "五、回测设置与结果", 1)
    _heading(doc, "5.1 回测设置", 2)
    _kv_table(doc, [
        ("回测区间", f"{bt_cfg['start']} ~ {bt_cfg['end']}"),
        ("样本内 / 样本外", f"2019-01-01 ~ 2022-12-31 / 2023-01-01 ~ {bt_cfg['end']}"),
        ("初始资金", f"{bt_cfg['initial_cash']:,.0f} 元"),
        ("股票池", f"{meta['universe_size']} 只（市值前 {meta['universe_requested']} 名）"),
        ("基准", f"{meta['benchmark']}（价格指数）"),
        ("佣金", "万 2.5，单笔最低 5 元，双边"),
        ("印花税", "卖出收取：2023-08-28 前 0.1%，之后 0.05%"),
        ("滑点", "单边 0.05%"),
        ("无风险利率", "1.5%（年化，用于夏普/索提诺）"),
        ("调仓 / 成交价", "月末信号，次一交易日开盘价"),
    ])

    _heading(doc, "5.2 核心指标", 2)
    show_metrics = ["累计收益率", "年化收益率", "年化波动率", "最大回撤", "夏普比率",
                    "索提诺比率", "卡玛比率", "月胜率", "超额收益(年化)", "信息比率",
                    "Beta", "年化换手率"]
    tbl = metrics_df.loc[[m for m in show_metrics if m in metrics_df.index]].copy()
    # pandas 3.x 会把「数值 + 日期」的混合列推断成字符串列，这里强制转回数值
    tbl = tbl.apply(pd.to_numeric, errors="coerce")
    display = tbl.astype(object)  # 单元格混排百分号/倍数/占位符，用 object 承载
    for row in display.index:
        for col in display.columns:
            v = tbl.loc[row, col]
            if pd.isna(v):
                display.loc[row, col] = "—"
            elif row in ("夏普比率", "索提诺比率", "卡玛比率", "信息比率", "Beta"):
                display.loc[row, col] = f"{v:.2f}"
            elif row == "年化换手率":
                display.loc[row, col] = f"{v:.1f}x"
            else:
                display.loc[row, col] = f"{v * 100:.2f}%"
    display = display.reset_index().rename(columns={"index": "指标"})
    _table(doc, display, widths=[3.4, 3.2, 3.2, 3.2])
    _para(doc, "注：\"全样本\"为 2019-01 至今；\"样本内\"\"样本外\"分别按上表切分后重新以区间首日归一计算。",
          size=8.5, color=MUTED)

    _heading(doc, "5.3 净值曲线与买卖点", 2)
    _figure(doc, FIGURE_DIR / "nav_curve.png",
            "图 2　策略与基准归一化净值（初始 = 1.0），▲ 为建仓/加仓点，▼ 为降仓点，虚线为样本内外分界")
    _para(doc,
          f"从净值曲线看：全样本策略累计 {m_all.get('累计收益率', 0) * 100:.1f}%、"
          f"年化 {m_all.get('年化收益率', 0) * 100:.1f}%，基准（沪深 300）累计 "
          f"{m_all.get('基准累计收益率', 0) * 100:.1f}%、年化 {m_all.get('基准年化收益率', 0) * 100:.1f}%。"
          f"分区间看，样本内（2019-2022）策略年化 {m_is.get('年化收益率', 0) * 100:.1f}%，"
          f"跑输基准的 {m_is.get('基准年化收益率', 0) * 100:.1f}%；样本外（2023 起）策略年化 "
          f"{m_oos.get('年化收益率', 0) * 100:.1f}%，明显好于基准的 "
          f"{m_oos.get('基准年化收益率', 0) * 100:.1f}%。"
          f"全样本最大回撤 {m_all.get('最大回撤', 0) * 100:.1f}%，"
          f"显著小于基准的 {m_all.get('基准最大回撤', 0) * 100:.1f}%，"
          f"主要因为择时在指数跌破 {timing_ma} 日均线时把仓位降到 {timing_low * 100:.0f}%。"
          f"图中 ▲ 为建仓/加仓、▼ 为降仓，虚线为样本内外分界。", indent=0.74)

    _figure(doc, FIGURE_DIR / "drawdown.png", "图 3　回撤对比：策略（面积） vs 基准（虚线）")
    _figure(doc, FIGURE_DIR / "monthly_heatmap.png",
            "图 4　月度收益热力图（红色=正收益、绿色=负收益，符合 A 股习惯；最后一列为全年累计）")
    _figure(doc, FIGURE_DIR / "exposure.png", "图 5　股票仓位与持仓只数")
    _figure(doc, FIGURE_DIR / "factor_ic.png", "图 6　候选因子 ICIR（红色为入选组合的因子）")

    # ---------------- 6 样本内外 ----------------
    _heading(doc, "六、样本内/样本外对比与超额收益来源", 1)
    _para(doc,
          "把区间切成两段是检验策略是否过拟合最直接的办法。样本内 2019-2022 包含了"
          "2019-2020 的成长股行情与 2021-2022 的调整；样本外 2023-2026 是存量博弈、"
          "红利与大盘价值占优的市场。下表把两段并排。", indent=0.74)

    seg_rows = []
    for name in ("样本内", "样本外"):
        m = summary["metrics"].get(name, {})
        seg_rows.append({
            "区间": name,
            "年化收益": f"{m.get('年化收益率', 0) * 100:.1f}%",
            "基准年化": f"{m.get('基准年化收益率', 0) * 100:.1f}%",
            "年化超额": f"{m.get('超额收益(年化)', 0) * 100:.1f}%",
            "最大回撤": f"{m.get('最大回撤', 0) * 100:.1f}%",
            "夏普": f"{m.get('夏普比率', 0):.2f}",
            "信息比率": f"{m.get('信息比率', 0):.2f}",
            "Beta": f"{m.get('Beta', 0):.2f}",
        })
    _table(doc, pd.DataFrame(seg_rows), widths=[2.2, 2.2, 2.2, 2.2, 2.2, 1.6, 1.8, 1.4])

    excess_is = m_is.get("超额收益(年化)", 0.0)
    excess_oos = m_oos.get("超额收益(年化)", 0.0)
    excess_diff = (excess_oos - excess_is) * 100
    if excess_diff >= 0:
        trend_text = (
            f"样本内年化超额 {excess_is * 100:.1f}%，样本外 {excess_oos * 100:.1f}%，"
            f"样本外反而更高 {excess_diff:.1f} 个百分点。这说明超额并非只存在于样本内，"
            "但也可能与 2023 年后红利/大盘价值风格和因子的暴露方向相契合，不宜过度解读。"
        )
    else:
        trend_text = (
            f"样本内年化超额 {excess_is * 100:.1f}%，样本外 {excess_oos * 100:.1f}%，"
            f"样本外衰减 {abs(excess_diff):.1f} 个百分点。这是多因子策略的常态："
            "因子溢价会随市场结构变化而衰减，样本内 IC 估计本身也有噪声。"
        )
    _para(doc,
          trend_text +
          f"值得注意的是样本外策略的 Beta 只有 {m_oos.get('Beta', 0):.2f}，"
          "说明择时叠加显著降低了市场暴露，收益更多来自选股而非市场上涨。", indent=0.74)

    _heading(doc, "6.1 超额收益从哪里来", 2)
    _bullet(doc, "选股 alpha：入选因子按其样本内 IC 符号调整方向后合成综合得分，得分靠前的组合"
                 "在下一期具有横截面区分度（见 4.2/4.3 的 IC 与 ICIR），这部分贡献稳定但幅度有限，"
                 f"样本内年化超额为 {excess_is * 100:.1f}%。")
    _bullet(doc, f"择时的仓位管理：关闭择时后最大回撤由 {m_all.get('最大回撤', 0) * 100:.1f}% "
                 f"扩大到 {_ab('关闭择时', '最大回撤'):.1f}%、年化收益由 "
                 f"{_ab('主策略（Top30 + ICIR加权 + 择时）', '年化收益率'):.1f}% 升到 "
                 f"{_ab('关闭择时', '年化收益率'):.1f}%——择时的价值集中在风险端，代价是牺牲收益。")
    _bullet(doc, "成本可控：月频调仓 + 30 只持仓，年化换手约 "
                 f"{m_all.get('年化换手率', 0):.1f} 倍，交易成本（佣金+印花税+滑点）"
                 f"拖累约 {(1 - (1 - 0.0009) ** (2 * m_all.get('年化换手率', 0))) * 100:.1f}%/年，"
                 "仍在 alpha 可覆盖的范围内。")
    _bullet(doc, "幸存者偏差（负面因素）：股票池按 2026 年市值取前 300，天然只包含活到今天的赢家，"
                 "这部分会让超额收益被高估，是本文最需要说明的局限。")

    # ---------------- 7 参数敏感性 ----------------
    if ablation is not None:
        _heading(doc, "七、参数敏感性与消融实验", 1)
        _para(doc,
              "为判断结论是否依赖某一组特定参数，逐项改动配置并重跑全样本回测。"
              "除被改动的那一项外，其余设置完全一致。", indent=0.74)
        ab = ablation.reset_index()
        for c in ("年化收益率", "最大回撤", "年化超额", "年化换手率"):
            if c in ab:
                ab[c] = ab[c].map(lambda v: f"{v:.2f}%" if pd.notna(v) else "—")
        for c in ("夏普比率", "卡玛比率"):
            if c in ab:
                ab[c] = ab[c].map(lambda v: f"{v:.2f}" if pd.notna(v) else "—")
        _table(doc, ab, widths=[5.0, 2.4, 2.4, 1.8, 1.8, 2.2, 2.2])

        _para(doc,
              f"几点观察：(1) 关闭择时后年化收益 {_ab('关闭择时', '年化收益率'):.1f}%、"
              f"最大回撤 {_ab('关闭择时', '最大回撤'):.1f}%，主策略为 "
              f"{_ab('主策略（Top30 + ICIR加权 + 择时）', '年化收益率'):.1f}% / "
              f"{_ab('主策略（Top30 + ICIR加权 + 择时）', '最大回撤'):.1f}%，"
              "属于典型的“用回撤换收益”，说明择时主要作用在风险端；"
              f"(2) 持仓由 20 只增至 50 只，年化收益 {_ab('持仓 20 只', '年化收益率'):.1f}% → "
              f"{_ab('持仓 50 只', '年化收益率'):.1f}%、最大回撤 "
              f"{_ab('持仓 20 只', '最大回撤'):.1f}% → {_ab('持仓 50 只', '最大回撤'):.1f}%，"
              "本样本上集中持仓的收益弹性更大，分散化并没有自动带来更高收益；"
              f"(3) 季度调仓年化换手降至 {_ab('季度调仓', '年化换手率') / 100:.1f} 倍、"
              f"年化收益 {_ab('季度调仓', '年化收益率'):.1f}%，说明月频调仓捕捉到的短周期信号更多，"
              "但也要付出更高的交易成本。", indent=0.74)

    # ---------------- 8 局限 ----------------
    _heading(doc, "八、已知局限与改进方向", 1)
    _bullet(doc, "幸存者偏差：股票池用当前市值排序，未使用历史成分股，会高估收益。"
                 "改进：接入沪深 300 历史成分股（按生效日期滚动）或全市场退市样本。")
    _bullet(doc, "分红口径：个股与基准都用不含分红的价格序列（个股不复权、基准为沪深 300 价格指数），"
                 "两边口径一致，超额收益的比较是公平的；代价是绝对收益被低估约 2 个百分点/年。"
                 "改进：个股改用后复权、基准改用沪深 300 全收益指数，可得到含分红的绝对收益。")
    _bullet(doc, "因子维度有限：目前只有量价因子，缺少基本面（估值、盈利、成长）与分析师预期数据，"
                 "无法做真正的多维度多因子模型。")
    _bullet(doc, "未考虑冲击成本与容量：组合规模假设为 1000 万元，策略容量远大于此，"
                 "但若资金量放大，停牌与涨跌停导致的无法成交比例会上升。")
    _bullet(doc, "未做行业中性：组合可能在某些行业上过度集中。改进：加入行业哑变量做中性化。")

    # ---------------- 9 AI 说明 ----------------
    _heading(doc, "九、AI 辅助说明与复现步骤", 1)
    _para(doc,
          "按课程要求，如实说明 AI 工具的使用范围：本项目的工程骨架、数据适配器、"
          "回测引擎、因子库、指标与绘图模块由 AI 编程助手（DeepSeek Harness）"
          "在提示词指导下生成，并由小组成员逐行阅读、运行验证与修改；"
          "策略思路（多因子 + 均线择时的两层结构）、因子方向的经济学解释、"
          "样本内/外切分方式与成本假设由小组成员确定，组员可对上述任何设计选择答辩。"
          "AI 生成部分均在本仓库中可追溯（提交记录 + 本文档说明），核心逻辑不依赖任何第三方"
          "回测框架，全部为自行实现。", indent=0.74)

    _heading(doc, "9.1 复现步骤", 2)
    for i, step in enumerate([
        "安装依赖：pip install -r requirements.txt（Python 3.12）",
        "抓取行情：python scripts/download_data.py（300 只约 10–20 分钟，接口限流会自动补抓）",
        "跑回测：python scripts/run_backtest.py（输出指标、图表、参数敏感性实验）",
        "启动看板：python -m qlab web，浏览器打开 http://127.0.0.1:8000 可在线改参数重跑",
        "生成报告：python -m qlab report（即本文档）",
        f"运行测试：python -m unittest discover -s tests（{n_tests} 个用例）",
    ], 1):
        _para(doc, f"{i}. {step}", size=10)

    _heading(doc, "9.2 目录结构", 2)
    tree = """quant/
├── qlab/                  # 主包
│   ├── data/              # 数据层：providers / cache / loader / quality
│   ├── factors/           # 计算层：library / preprocessing / analysis
│   ├── strategy/          # 策略层：multifactor / timing
│   ├── backtest/          # 回测层：engine / broker
│   ├── analytics/         # 指标与导出：metrics / report
│   ├── viz/               # 绘图：chart / figures
│   ├── webapp/            # Web 看板：server + static
│   ├── config.py          # 全局参数
│   └── cli.py             # 命令行入口
├── scripts/               # 数据抓取 / 回测流水线
├── tests/                 # 单元测试
├── data/raw/              # 行情缓存（CSV + manifest）
├── output/                # 图表、指标表、报告
├── README.md
└── requirements.txt"""
    p = doc.add_paragraph()
    p.paragraph_format.line_spacing = 1.0
    run = p.add_run(tree)
    _set_cn_font(run, name="Consolas", size=8.5)

    _para(doc, "")
    out_path = out_dir / "项目技术报告.docx"
    doc.save(out_path)
    log.info("技术报告已保存：%s", out_path)
    return out_path
