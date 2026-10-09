import os
from pathlib import Path

from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Inches, Pt, RGBColor

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = Path(os.environ.get("ACADEMIC_DOC_OUTPUT", ROOT / "docs" / "requirements-report.docx"))


def set_run_font(run, name="Microsoft YaHei", size=10.5, bold=None, color="000000"):
    run.font.name = name
    rpr = run._element.get_or_add_rPr()
    for key in ("w:eastAsia", "w:ascii", "w:hAnsi"):
        rpr.rFonts.set(qn(key), name)
    run.font.size = Pt(size)
    if bold is not None:
        run.bold = bold
    run.font.color.rgb = RGBColor.from_string(color)


def set_cell_shading(cell, fill):
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = tc_pr.find(qn("w:shd"))
    if shd is None:
        shd = OxmlElement("w:shd")
        tc_pr.append(shd)
    shd.set(qn("w:fill"), fill)


def set_cell_margins(cell, top=90, start=100, bottom=90, end=100):
    tc_pr = cell._tc.get_or_add_tcPr()
    tc_mar = tc_pr.first_child_found_in("w:tcMar")
    if tc_mar is None:
        tc_mar = OxmlElement("w:tcMar")
        tc_pr.append(tc_mar)
    for margin, value in (("top", top), ("start", start), ("bottom", bottom), ("end", end)):
        node = tc_mar.find(qn(f"w:{margin}"))
        if node is None:
            node = OxmlElement(f"w:{margin}")
            tc_mar.append(node)
        node.set(qn("w:w"), str(value))
        node.set(qn("w:type"), "dxa")


def set_table_borders(table, color="D9D9D9", size="6"):
    tbl_pr = table._tbl.tblPr
    borders = tbl_pr.first_child_found_in("w:tblBorders")
    if borders is None:
        borders = OxmlElement("w:tblBorders")
        tbl_pr.append(borders)
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        tag = borders.find(qn(f"w:{edge}"))
        if tag is None:
            tag = OxmlElement(f"w:{edge}")
            borders.append(tag)
        tag.set(qn("w:val"), "single")
        tag.set(qn("w:sz"), size)
        tag.set(qn("w:color"), color)


def set_repeat_table_header(row):
    tr_pr = row._tr.get_or_add_trPr()
    node = OxmlElement("w:tblHeader")
    node.set(qn("w:val"), "true")
    tr_pr.append(node)


def prevent_row_split(row):
    row._tr.get_or_add_trPr().append(OxmlElement("w:cantSplit"))


def add_table(doc, headers, rows, widths=None, font_size=8.8, first_col_center=True):
    table = doc.add_table(rows=1, cols=len(headers))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    set_table_borders(table)
    header = table.rows[0]
    set_repeat_table_header(header)
    prevent_row_split(header)
    for i, value in enumerate(headers):
        cell = header.cells[i]
        set_cell_shading(cell, "244A7C")
        set_cell_margins(cell)
        cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        p = cell.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.keep_with_next = True
        set_run_font(p.add_run(str(value)), size=font_size, bold=True, color="FFFFFF")
        if widths:
            cell.width = Inches(widths[i])
    for row_index, values in enumerate(rows):
        row = table.add_row()
        prevent_row_split(row)
        if row_index % 2:
            for cell in row.cells:
                set_cell_shading(cell, "F3F6FA")
        for i, value in enumerate(values):
            cell = row.cells[i]
            set_cell_margins(cell)
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            p = cell.paragraphs[0]
            p.paragraph_format.line_spacing = 1.1
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER if (i == 0 and first_col_center) else WD_ALIGN_PARAGRAPH.LEFT
            set_run_font(p.add_run(str(value)), size=font_size)
            if widths:
                cell.width = Inches(widths[i])
    doc.add_paragraph().paragraph_format.space_after = Pt(0)
    return table


def add_body(doc, text):
    p = doc.add_paragraph(style="Normal")
    p.paragraph_format.first_line_indent = Pt(21)
    p.paragraph_format.space_after = Pt(5)
    p.paragraph_format.line_spacing = 1.3
    set_run_font(p.add_run(text))
    return p


def add_bullets(doc, items):
    for item in items:
        p = doc.add_paragraph(style="List Bullet")
        p.paragraph_format.space_after = Pt(3)
        p.paragraph_format.line_spacing = 1.2
        set_run_font(p.add_run(item))


def add_page_number(paragraph):
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = paragraph.add_run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = "PAGE"
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    run._r.extend((begin, instr, end))
    set_run_font(run, size=9, color="666666")


def add_requirement(doc, req):
    doc.add_heading(f"{req['id']} {req['name']}", level=2)
    add_table(doc, ["优先级", "来源", "人工核实", "当前状态"],
              [[req["priority"], req["source"], req["verified"], req["status"]]],
              [0.75, 2.25, 2.0, 1.55], font_size=8.6)
    add_body(doc, req["description"])
    add_table(doc, ["项目", "定义"], [
        ["前置条件", req["pre"]], ["输入与约束", req["input"]], ["正常处理", req["normal"]],
        ["异常处理", req["error"]], ["输出", req["output"]], ["验收标准", req["acceptance"]],
    ], [1.05, 5.55], font_size=8.8)


def setup_document():
    doc = Document()
    section = doc.sections[0]
    section.page_width = Cm(21.0)
    section.page_height = Cm(29.7)
    section.top_margin = Cm(1.8)
    section.bottom_margin = Cm(1.7)
    section.left_margin = Cm(1.9)
    section.right_margin = Cm(1.9)
    props = doc.core_properties
    props.title = "学业负荷雷达需求分析文档"
    props.subject = "软件工程需求分析个人实验"
    props.author = "个人实验提交者"
    normal = doc.styles["Normal"]
    normal.font.name = "Microsoft YaHei"
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    normal.font.size = Pt(10.5)
    for style_name, size in (("Title", 23), ("Heading 1", 15.5), ("Heading 2", 12.5), ("Heading 3", 11)):
        style = doc.styles[style_name]
        style.font.name = "Microsoft YaHei"
        style._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
        style.font.size = Pt(size)
        style.font.color.rgb = RGBColor(0, 0, 0)
        style.font.bold = True
        style.paragraph_format.keep_with_next = True
        style.paragraph_format.space_before = Pt(12 if style_name == "Heading 1" else 8)
        style.paragraph_format.space_after = Pt(6)
        if style_name == "Title":
            ppr = style._element.get_or_add_pPr()
            border = ppr.find(qn("w:pBdr"))
            if border is not None:
                ppr.remove(border)
    header = section.header.paragraphs[0]
    header.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    set_run_font(header.add_run("学业负荷雷达 需求分析个人实验"), size=8.5, color="666666")
    add_page_number(section.footer.paragraphs[0])
    return doc


def add_front_matter(doc):
    title = doc.add_paragraph(style="Title")
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title.paragraph_format.space_before = Pt(118)
    title_ppr = title._p.get_or_add_pPr()
    title_border = title_ppr.find(qn("w:pBdr"))
    if title_border is not None:
        title_ppr.remove(title_border)
    set_run_font(title.add_run("学业负荷雷达需求分析文档"), size=23, bold=True)
    subtitle = doc.add_paragraph()
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    subtitle.paragraph_format.space_before = Pt(14)
    set_run_font(subtitle.add_run("自定义选题  需求分析文档生成  AI 协作个人实验"), size=13, color="333333")
    meta = doc.add_paragraph()
    meta.alignment = WD_ALIGN_PARAGRAPH.CENTER
    meta.paragraph_format.space_before = Pt(72)
    set_run_font(meta.add_run("版本 1.2\n2026 年 10 月 8 日"), size=11, color="555555")
    note = doc.add_paragraph()
    note.alignment = WD_ALIGN_PARAGRAPH.CENTER
    note.paragraph_format.space_before = Pt(70)
    set_run_font(note.add_run("本报告按个人实验要求编写  提交前由学生本人完成最终审阅"), size=9.5, color="666666")
    doc.add_page_break()
    doc.add_heading("文档说明", level=1)
    add_body(doc, "本报告把学业负荷雷达作为自定义工具类选题。文档定义用户问题、需求边界、功能与非功能需求、异常处理、数据格式、验收标准和来源追溯关系，并记录 AI 协作过程及人工核实结果。需求描述以可测试为原则；已经实现的能力与尚未实现的需求分别标注，避免把原型现状写成已经完成的承诺。")
    add_table(doc, ["版本", "日期", "修改内容", "结论"], [
        ["0.1", "2026-09-21", "形成基于原型的需求初稿", "结构不足"],
        ["1.0", "2026-09-23", "按个人评分标准重写并加入协作 核实和追溯记录", "本次需求基线"],
        ["1.1", "2026-10-06", "同步 EDF 排程 预测可信度 进度更新和当前测试证据", "实现对齐版"],
        ["1.2", "2026-10-08", "增加进度矛盾预警和自适应排程窗口", "结果解释增强版"],
    ], [0.7, 1.05, 3.95, 0.9], font_size=8.8)
    add_body(doc, "来源编号说明：C 表示课程实验要求；U 表示学生在 AI 对话中的明确决定或修正；I 表示现有实现；T 表示自动化测试证据；A 表示 AI 建议。只有 A 类来源且未获人工确认的内容不能作为最终关键结论。")
    doc.add_heading("内容目录", level=1)
    toc = [[str(i), name] for i, name in enumerate([
        "选题背景与问题陈述", "目标用户与使用场景", "目标 假设与范围", "功能需求", "业务规则与核心算法",
        "数据需求", "接口与运行环境", "非功能需求", "故障模式与处理", "验收测试与追溯",
        "AI 协作记录", "人工核实说明", "需求基线与待办"], start=1)]
    add_table(doc, ["章节", "内容"], toc, [0.8, 5.8], font_size=9.2)


def add_context_sections(doc):
    doc.add_heading("1 选题背景与问题陈述", level=1)
    doc.add_heading("1.1 选题说明", level=2)
    add_body(doc, "课程允许学生完全自定义选题。本项目选择学业负荷雷达，属于个人效率工具：它帮助同时承担多门课程、实验和项目任务的学生判断截止日期前的累计工作量是否超过个人可用时间。该选题不替代教学平台，也不评价学习成绩。")
    doc.add_heading("1.2 问题陈述", level=2)
    add_body(doc, "普通待办工具通常按任务分别显示截止日期。学生真正面临的是共享时间容量：一个较晚截止的任务需要与此前所有未完成任务竞争同一段时间。若只比较单个任务的工时与截止日期前时间，就会低估多个任务叠加后的风险。")
    add_body(doc, "进行中任务还有第二个不确定性。最初填写的预计工时会随任务推进而失真，系统需要结合已经投入的时间、当前完成比例和过去任务的实际与预计偏差，重新估算剩余工时。估算必须说明使用了哪些数据，不能只给出无法解释的风险标签。")
    doc.add_heading("1.3 需求结论", level=2)
    add_bullets(doc, [
        "系统必须把同一截止日期前的全部未完成任务纳入累计负荷，而不是逐条独立判断。",
        "系统必须允许补录进行中任务和历史任务，避免只能从启用系统当天开始积累数据。",
        "系统必须区分批量导入与批量处理；批量处理指一次重新分析已经填写的全部任务。",
        "系统必须显示剩余工时、累计负荷、可用容量、建议顺序和计算依据，便于用户复核。",
        "系统应使用最早截止期优先的可解释排程，报告预计完成日期和截止前缺口。",
    ])
    doc.add_heading("2 目标用户与使用场景", level=1)
    add_table(doc, ["用户类型", "特征", "主要目标", "典型频率"], [
        ["核心用户", "同时承担多门课程任务的大学生", "提前发现任务叠加造成的超载", "每日查看 每周维护"],
        ["扩展用户", "承担课程项目和个人任务的学生", "比较课程任务与项目任务的时间冲突", "每周"],
        ["维护者", "负责本地部署和算法维护的学生", "复核数据与风险规则并运行测试", "版本迭代时"],
    ], [1.0, 2.2, 2.6, 0.9], font_size=8.8)
    doc.add_heading("2.1 核心场景", level=2)
    add_table(doc, ["编号", "触发条件", "用户行为", "期望结果"], [
        ["SC-01", "收到新任务", "录入名称 课程 截止日期 预计工时和优先级", "任务立即进入组合风险分析"],
        ["SC-02", "任务已经开始", "补充开始时间 投入工时和完成比例", "系统更新预测剩余工时"],
        ["SC-03", "首次使用系统", "导入过去已完成任务", "建立个人估时偏差基线"],
        ["SC-04", "任务集中出现", "批量处理当前所有已填写任务", "发现最早超载日期和最高风险任务"],
        ["SC-05", "任务完成", "填写实际用时并标记完成", "更新按时率 效率和缓冲建议"],
        ["SC-06", "时间条件变化", "调整每日可用时间或缓冲比例", "全部风险结果重新计算"],
    ], [0.65, 1.5, 2.5, 2.05], font_size=8.7)
    doc.add_heading("3 目标 假设与范围", level=1)
    doc.add_heading("3.1 产品目标与成功指标", level=2)
    add_table(doc, ["目标", "可度量指标", "验证方式"], [
        ["降低隐性超载", "累计需求大于可用容量的截止日期必须标为高风险", "固定数据集计算对照"],
        ["改进剩余工时估算", "有进度和历史数据时返回预测方法及数值分解", "单元测试与接口检查"],
        ["降低使用成本", "新用户在 3 分钟内完成设置并录入第一条任务", "5 人可用性测试"],
        ["保持可解释", "活动任务显示本任务剩余 累计剩余 累计可用和原因", "界面验收"],
    ], [1.35, 3.6, 1.75], font_size=8.8)
    doc.add_heading("3.2 关键假设", level=2)
    add_bullets(doc, [
        "用户能给出大致合理的预计工时，并愿意在任务推进后更新投入时间和进度。",
        "完成比例是主观数据，因此只作为估算信号，并保留历史效率权重。",
        "第一阶段按设备本地日期计算，不处理跨时区协作。",
        "历史任务不足时不得假装模型已经个性化，默认历史效率系数为 1.0。",
    ])
    doc.add_heading("3.3 纳入范围", level=2)
    add_bullets(doc, [
        "普通 进行中和历史任务的录入与保存；历史和进行中任务 CSV 导入。",
        "个人可用时间 缓冲比例 跟踪窗口和提前完成阈值设置。",
        "剩余工时预测 累计容量分析 风险评分和解释。",
        "批量处理已填写任务 完成跟踪 近期统计与缓冲建议；任务支持更新累计投入工时和进度。",
        "本地 SQLite 持久化 响应式网页和基础错误提示。",
    ])
    doc.add_heading("3.4 范围外事项", level=2)
    add_bullets(doc, [
        "多用户账号 组织权限 学校统一身份认证和云端同步。",
        "自动读取教务系统 邮件 日历或第三方待办平台。",
        "自动替用户调整截止日期 删除任务或做学业决策。",
        "使用大模型直接判断任务难度；该能力需要足够分类样本和隐私评估后再立项。",
        "原生移动端应用 多人协作排期和实时通知。",
    ])


def get_requirements():
    return [
        dict(id="FR-01", name="普通任务管理", priority="高 P0", source="C01 U01 I01", verified="人工确认核心流程", status="已实现核心能力", description="用户能够创建普通任务并查看其状态。普通任务用于尚未开始或暂不记录进度的课程任务。", pre="服务可访问；用户已知任务截止日期和预计工时。", input="title 必填非空；course 可空；due_date 使用 YYYY-MM-DD；estimated_hours 大于 0；priority 为 1 2 3。", normal="系统校验输入，生成唯一任务编号，写入数据库并重新计算仪表盘。", error="空名称 非法日期 非正数工时或非法优先级返回 400，并且不得写入记录。", output="201 与任务编号；仪表盘显示任务 剩余工时 风险和解释。", acceptance="AC-01：有效数据保存后可查询。AC-02：预计工时为 0 时返回 400，数据库条数不变。"),
        dict(id="FR-02", name="进行中任务录入与进度更新", priority="高 P0", source="U04 I01", verified="人工明确提出", status="已实现并支持更新", description="用户能够录入在启用系统之前已经开始但尚未完成的任务，并在后续直接更新累计投入工时和完成比例，使最新观察值进入剩余负荷计算。", pre="任务未完成；用户至少知道预计总工时 已投入工时或当前进度。", input="录入时 started_at 为 ISO 日期时间且可空；spent_hours 不小于 0；progress_percent 为 0 至 99；更新时 PUT /api/tasks/{id} 可提供 spent_hours 和或 progress_percent。", normal="系统保存为 todo，调用剩余工时预测，再参与累计组合风险；首次补充投入或进度时自动记录开始时间。", error="进度达到 100 投入工时为负 预计工时非正数 日期非法或试图更新已完成任务时拒绝保存并给出原因。", output="录入返回 201 和任务编号；更新返回 id spent_hours progress_percent started_at；风险面板显示最新预测。", acceptance="AC-03：录入投入和进度后返回明确预测方法。AC-04：进度 100 时返回 400。AC-04a：更新进度后重新读取任务得到新投入工时和进度。"),
        dict(id="FR-03", name="历史任务补录", priority="中 P1", source="U03 I01", verified="人工明确提出", status="已实现核心能力", description="用户能够补录系统启用前已经完成的任务，以增加个性化估时和完成表现的样本。", pre="任务已经完成；用户能够说明原截止日期 完成时间 预计与实际工时。", input="completed_at 必填 ISO 日期时间；estimated_hours 和 actual_hours 大于 0；data_precision 为 exact approx rough；stress_level 可为 1 至 5。", normal="系统计算 early on_time late，标记 historical，不计入未来负荷；非 rough 且工时完整的记录参与效率统计。", error="完成时间 日期 工时或精度枚举非法时拒绝写入；rough 记录可保存但不参与效率系数。", output="201 任务编号和 historical=true；近期表现更新样本数。", acceptance="AC-05：提前超过阈值的记录分类为 early。AC-06：rough 记录不改变历史效率中位数。"),
        dict(id="FR-04", name="个人容量与缓冲设置", priority="高 P0", source="U02 I01", verified="人工确认缓冲定义", status="已实现核心能力", description="用户设置工作日和周末每天可用于任务的时间，以及为突发情况保留的安全缓冲比例。缓冲不是额外时间，而是从理论容量中扣除的比例。", pre="用户能估计日均可用时间。", input="weekday_hours weekend_hours 为 0 至 24；buffer_ratio 为 0 至 0.8；tracking_window_days 为 7 至 365；early_threshold_hours 为 1 至 168。", normal="保存设置后立即对全部任务重新计算。可用容量等于日期区间内每日容量之和乘以 1 减缓冲比例。", error="缺失或非数值字段返回 400；超范围数值按边界夹取并在返回值中显示。", output="更新后的设置对象和新的任务风险。", acceptance="AC-07：理论容量 10 小时 缓冲 20% 时可用容量为 8 小时。AC-08：缓冲 1.0 被限制为 0.8。"),
        dict(id="FR-05", name="剩余工时预测", priority="高 P0", source="U04 I01 T01", verified="人工确认方法边界", status="已实现并有单测", description="系统使用进度反推与历史效率两类信号估算进行中任务的剩余工时，并返回预测方法、可信度和进度矛盾预警。算法规则见第 5 章。", pre="任务为未完成状态；预计工时有效。", input="estimated_hours spent_hours progress_percent，以及可靠历史记录形成的效率系数和样本数量。", normal="有投入和进度时融合进度反推与历史预测；只有投入时使用历史总工时减投入；未开始时使用历史校准预计工时；当进度外推总工时与历史校准总工时相差过大时提示核对并降低可信度。", error="缺少历史样本时效率系数为 1.0；除数下限和剩余工时下限防止除零及负值。", output="remaining_hours predicted_total_hours prediction_method historical_efficiency_ratio prediction_confidence confidence_reason prediction_warning。", acceptance="AC-09：固定输入与公式误差不超过 0.01 小时。AC-10：无历史样本时系数为 1.0 且方法可解释。AC-10a：有实际进度和至少 5 个可靠样本时可信度为 high。AC-10b：进度外推总工时与历史校准总工时相差超过 2.5 倍或低于 0.4 倍时返回预警且不保持虚假的 high。"),
        dict(id="FR-06", name="累计组合风险", priority="高 P0", source="U04 I01 T01", verified="人工指出旧分析缺陷", status="已实现并有单测", description="系统评估某截止日期时，必须累计所有截止日期不晚于该日期的未完成任务剩余工时，避免遗漏此前需要完成的任务。", pre="系统中存在至少一个未完成任务；容量设置有效。", input="全部活动任务的 due_date 与 remaining_hours；个人每日容量和 buffer_ratio。", normal="按每个任务截止日期计算累计剩余工时 累计可用容量 负荷比和风险等级，并计算时间余量与每日所需工时。", error="截止日期已过直接判为高风险；累计容量为 0 时使用保护分母并说明没有可用时间。", output="cumulative_remaining_hours cumulative_available_hours workload_ratio slack_hours required_daily_hours risk risk_score explanation。", acceptance="AC-11：较晚任务累计剩余包含较早任务。AC-12：累计需求超过容量时 risk=high。"),
        dict(id="FR-07", name="批量处理当前任务", priority="高 P0", source="U05 I01 T01", verified="人工纠正 AI 误解", status="已实现并有单测", description="批量处理是对数据库中已经填写的全部任务执行一次统一重算与汇总，不等同于批量导入。", pre="系统可读取任务和设置；任务数量可以为 0。", input="数据库中的全部任务和当前设置；用户无需上传文件。", normal="重新评估全部任务，汇总活动数 完成数 剩余总工时 风险分布 超载数 排程缺口 最早超载日期 最高风险任务和前三项行动建议。", error="任务数据损坏或计算失败时返回 400 和错误信息，不展示旧结果为新结果。空任务集返回全零摘要。", output="批量摘要及每条重新评估的任务，同时返回 schedule_missed_count next_actions 和 algorithm。", acceptance="AC-13：处理数等于数据库任务数。AC-14：组合超载时返回最早超载日期。AC-15：按钮不打开文件选择器。AC-15a：同截止日期任务按优先级生成建议顺序。"),
        dict(id="FR-08", name="完成与跟踪模式", priority="中 P1", source="U02 I01", verified="人工确认跟踪目标", status="已实现核心能力", description="开启跟踪模式后，用户标记完成任务时必须填写实际工时；系统记录完成时间并按提前阈值分类。", pre="任务存在且状态为 todo。", input="任务编号 status=done actual_hours 大于 0；可选 stress_level 1 至 5。", normal="更新状态 投入工时 进度 100 完成时间和 completion_type，并刷新统计。", error="任务不存在返回 404；实际工时非正数或状态非法返回 400。用户取消输入时不修改任务。", output="任务编号 done 状态和 early on_time late 分类。", acceptance="AC-16：完成后任务剩余工时为 0。AC-17：截止日前至少 24 小时完成默认分类 early。"),
        dict(id="FR-09", name="近期统计与缓冲建议", priority="中 P1", source="U02 I01", verified="人工确认不自动执行", status="已实现核心能力", description="系统在指定滚动窗口内统计按时率 实际与预计工时中位比 提前和临近截止效率，并给出缓冲比例建议。截止前效率提升只用于解释，不能成为自动降低缓冲的单一依据。", pre="跟踪窗口有效；允许样本不足。", input="最近 7 至 365 天完成记录，默认 30 天；当前 buffer_ratio。", normal="样本至少 5 个时依据按时率 逾期率和估时偏差建议调整；单次变化不超过 5 个百分点，建议范围 5% 至 40%。", error="少于 5 个样本时 can_adjust=false；没有可比较样本时 deadline_effect=null。", output="样本数 按时率 中位估时比 截止效应 当前与建议缓冲 文字原因。", acceptance="AC-18：4 个样本不得给出可执行调整。AC-19：任何一次建议变化绝对值不超过 0.05。"),
        dict(id="FR-10", name="CSV 批量导入", priority="低 P2", source="U03 U04 I01", verified="人工确认数据用途", status="已实现基础版本", description="系统支持分别导入历史任务和进行中任务，用于快速补齐已有数据。该能力与 FR-07 的批量处理相互独立。", pre="用户准备 UTF-8 CSV；首行包含英文或约定中文字段名。", input="历史任务字段含 title course due_date completed_at estimated_hours actual_hours data_precision；进行中任务含 started_at spent_hours progress_percent priority 等。", normal="逐行校验；有效行写入同一 batch_id；无效行记录行号和原因；允许部分成功。", error="空文件整体拒绝；单行错误不回滚其他有效行；重复导入检测尚未实现。", output="batch_id created 编号列表 errors 行号列表。", acceptance="AC-20：一行有效一行非法时有效行保存且 errors 包含行号。AC-21：空 CSV 返回 400。"),
        dict(id="FR-11", name="仪表盘与持久化", priority="高 P0", source="C01 I01", verified="代码与测试核对", status="已实现核心能力", description="系统通过浏览器显示任务 容量和近期统计，并使用 SQLite 保存数据。正常重启后数据应保留。", pre="本地服务启动；数据目录可写。", input="HTTP 请求 任务记录和设置记录。", normal="首页加载设置 仪表盘和近期统计；数据库文件存储任务与设置。", error="数据目录不可写或数据库异常时应返回明确错误并保留原数据；当前版本对此仍需完善。", output="HTML CSS JavaScript 页面 JSON API 和 SQLite 数据文件。", acceptance="AC-22：创建任务后重启服务仍可读取。AC-23：390px 与 1280px 宽度下核心表单无水平遮挡。"),
        dict(id="FR-12", name="任务删除和错误反馈", priority="中 P1", source="C01 I01", verified="代码核对", status="已实现并有边界待测", description="用户能够删除错误任务。所有失败操作必须提供可理解的原因，避免静默失败。", pre="任务编号存在或请求能够被解析。", input="DELETE /api/tasks/{id}；各表单的无效字段。", normal="删除后返回编号并刷新仪表盘。", error="非法编号返回 400；不存在的删除目标返回 404；其他失败返回 error 字段。", output="删除结果或错误对象 error。", acceptance="AC-24：存在任务删除后不可查询。AC-25：不存在任务返回 404 而不是伪成功。"),
    ]


def add_requirements(doc):
    doc.add_heading("4 功能需求", level=1)
    reqs = get_requirements()
    add_table(doc, ["编号", "名称", "优先级", "来源"], [[r["id"], r["name"], r["priority"], r["source"]] for r in reqs],
              [0.7, 2.6, 1.0, 2.35], font_size=8.8)
    for req in reqs:
        add_requirement(doc, req)


def add_rules_data_interfaces(doc):
    doc.add_heading("5 业务规则与核心算法", level=1)
    doc.add_heading("5.1 可用容量与缓冲", level=2)
    add_body(doc, "在今天至截止日期的闭区间内，工作日使用 weekday_hours，周末使用 weekend_hours。理论容量为每日容量之和。可用容量 C 等于理论容量乘以 (1 - B)，其中 B 是缓冲比例，限制在 0 至 0.8。截止日期早于今天时天数按 0 处理。")
    doc.add_heading("5.2 历史效率系数", level=2)
    add_body(doc, "对状态为 done、actual_hours 有效、estimated_hours 大于 0 且 data_precision 不为 rough 的任务，计算 actual_hours / estimated_hours。历史效率系数 H 取这些比值的中位数；没有可靠样本时 H=1.0。中位数用于降低极端任务的影响。")
    doc.add_heading("5.3 进行中任务预测", level=2)
    add_table(doc, ["条件", "计算规则", "方法标识"], [
        ["S>0 且 P>0", "Rprogress=max(S/P-S,0.1)；Rhistory=max(E×H-S,0.1)；w=min(0.75,max(0.4,P))；R=w×Rprogress+(1-w)×Rhistory", "progress_and_history"],
        ["S>0 且 P=0", "R=max(E×H-S,0.1)", "history_after_spent_time"],
        ["S=0", "R=max(E×H,0.1)", "historical_efficiency 或 initial_estimate"],
    ], [1.2, 4.25, 1.25], font_size=8.5)
    add_body(doc, "符号定义：E 为预计总工时，S 为已投入工时，P 为 0 至 0.99 的完成比例，R 为预测剩余工时。进度越高，进度反推的权重越高，但上限为 0.75，避免主观进度完全覆盖历史证据。若 S/P 推出的总工时与 E×H 的比值低于 0.4 或高于 2.5，系统保留两类信号但返回 prediction_warning，并将可信度最多降为 medium。")
    doc.add_heading("5.4 可信度分级", level=2)
    add_body(doc, "系统根据预测证据计算可信度，不把可信度当作风险等级，也不表示完成概率。已有实际投入工时与完成比例时增加证据分；完成比例至少 50% 时再增加证据分；可靠历史样本达到 2 个或 5 个时分别增加不同权重；exact 数据增加证据，rough 数据降低证据。进度与投入明显不一致时扣除证据分。总分至少 4 为 high，至少 2 为 medium，否则为 low，并返回 confidence_reason 解释依据。")
    doc.add_heading("5.5 组合负荷与风险等级", level=2)
    add_body(doc, "对截止日期 d，累计负荷 L(d) 是所有未完成且截止日期不晚于 d 的任务剩余工时之和。负荷比 Q=L(d)/max(C(d),0.1)。若任务已逾期或 Q>1，风险为高；若 Q≥0.7 或剩余天数不超过 2，风险为中；其余为低。")
    add_body(doc, "风险分数由容量压力、截止紧迫度和优先级构成：容量部分为 min(65,Q×50)；剩余不超过 2 天加 20 分，不超过 5 天加 10 分；优先级 3 加 15 分，优先级 2 加 7 分；总分上限 100。风险等级以业务规则为准，分数用于排序。")
    doc.add_heading("5.6 最早截止期优先排程", level=2)
    add_body(doc, "在风险评分之外，系统按截止日期升序、同日优先级降序和录入顺序稳定排序，逐日分配扣除缓冲后的可用容量。每项活动任务得到 recommended_rank、projected_finish_date、shortfall_hours 和 schedule_status。任务在截止日期仍有未分配工时则标记为 late；预计完成日晚于截止日期也标记为 late。排程窗口默认至少 730 天，并根据总剩余工时和每周可用容量自适应延长，最多模拟 3650 天；可用容量为零时保留 730 天窗口并明确无法排完。该模拟用于提供行动顺序和可验证的缺口，不会自动修改用户的截止日期或任务优先级。")
    doc.add_heading("5.7 缓冲调整规则", level=2)
    add_bullets(doc, [
        "完成样本少于 5 个时不调整。",
        "按时率低于 80% 估时中位比大于 1.1 或逾期率高于 20% 时增加缓冲的证据增强。",
        "按时率至少 90% 估时中位比小于 0.95 且没有逾期时可建议降低缓冲。",
        "单次变化限制在正负 5 个百分点，最终范围为 5% 至 40%。",
        "临近截止效率提高只显示提醒，不因此直接降低缓冲。",
    ])
    doc.add_heading("6 数据需求", level=1)
    task_fields = [
        ["id", "整数", "是", "自增正整数", "任务唯一标识"], ["title", "文本", "是", "去空白后非空", "任务名称"],
        ["course", "文本", "否", "空值转空字符串", "课程或项目"], ["due_date", "日期", "是", "YYYY-MM-DD", "截止日期"],
        ["estimated_hours", "小数", "是", ">0", "预计总工时"], ["priority", "整数", "是", "1 2 3", "低 中 高"],
        ["status", "枚举", "是", "todo done", "完成状态"], ["started_at", "日期时间", "否", "ISO 8601", "开始时间"],
        ["completed_at", "日期时间", "否", "ISO 8601", "完成时间"], ["actual_hours", "小数", "否", ">0", "最终实际工时"],
        ["spent_hours", "小数", "是", ">=0", "已投入工时"], ["progress_percent", "小数", "是", "0 至 100", "完成比例"],
        ["completion_type", "枚举", "否", "early on_time late", "完成时机分类"], ["stress_level", "整数", "否", "1 至 5", "主观压力"],
        ["data_precision", "枚举", "是", "exact approx rough", "数据精度"], ["import_batch_id", "文本", "否", "12 位批次标识", "导入追踪"],
    ]
    add_table(doc, ["字段", "类型", "必填", "格式或范围", "含义"], task_fields, [1.35, 0.75, 0.55, 1.65, 2.35], font_size=8.2)
    doc.add_heading("6.1 CSV 格式", level=2)
    add_body(doc, "CSV 使用 UTF-8 编码和首行字段名。英文和约定中文字段名均可识别。日期使用 YYYY-MM-DD，日期时间使用 YYYY-MM-DDTHH:MM。数值字段不得带小时或百分号等单位文本。导入结果必须保留批次编号、成功记录编号和失败行号。")
    doc.add_heading("6.2 输出数据", level=2)
    add_table(doc, ["字段", "类型", "含义"], [
        ["remaining_hours", "小数 小时", "当前任务预测剩余工时"], ["predicted_total_hours", "小数 小时", "预测总工时"],
        ["cumulative_remaining_hours", "小数 小时", "截至相应日期的累计剩余工时"], ["cumulative_available_hours", "小数 小时", "扣除缓冲后的累计可用工时"],
        ["workload_ratio", "小数", "累计负荷除以累计容量"], ["slack_hours", "小数 小时", "累计可用容量减累计剩余负荷"],
        ["required_daily_hours", "小数 小时", "按剩余天数平均每天需要投入的工时"], ["risk", "枚举", "high medium low"],
        ["risk_score", "整数", "0 至 100 的排序分数"], ["prediction_confidence", "枚举", "high medium low"],
        ["confidence_reason", "文本", "预测可信度的证据说明"], ["prediction_warning", "文本 可空", "进度与投入不一致时的核对提醒"], ["recommended_rank", "整数", "EDF 建议处理顺序"],
        ["projected_finish_date", "日期", "按当前容量预计完成日期"], ["shortfall_hours", "小数 小时", "截止日期时尚未排出的工时"],
        ["schedule_status", "枚举", "on_track late completed"], ["schedule_note", "文本", "排程结果说明"],
        ["explanation", "文本", "包含关键数值的风险原因"],
    ], [2.25, 1.25, 3.25], font_size=8.7)
    doc.add_heading("7 接口与运行环境", level=1)
    endpoints = [
        ["GET", "/api/dashboard", "无", "汇总和全部任务"], ["GET", "/api/insights", "window 可选", "近期统计与建议"],
        ["POST", "/api/tasks", "普通任务 JSON", "201 与 id"], ["POST", "/api/tasks/ongoing", "进行中任务 JSON", "201 与 id"],
        ["POST", "/api/tasks/history", "历史任务 JSON", "201 与 id"], ["POST", "/api/tasks/batch/process", "空 JSON", "组合摘要"],
        ["POST", "/api/tasks/import", "历史 CSV 文本", "批次结果"], ["POST", "/api/tasks/ongoing/import", "进行中 CSV 文本", "批次结果"],
        ["PUT", "/api/settings", "设置 JSON", "标准化后的设置"], ["PUT", "/api/tasks/{id}", "状态和实际工时，或 spent_hours / progress_percent", "完成分类或更新进度"],
        ["DELETE", "/api/tasks/{id}", "路径 id", "删除结果"],
    ]
    add_table(doc, ["方法", "路径", "主要输入", "主要输出"], endpoints, [0.65, 2.25, 2.0, 1.8], font_size=8.4)
    doc.add_heading("7.1 环境与依赖", level=2)
    add_bullets(doc, [
        "服务端使用 Python 标准库 ThreadingHTTPServer 和 SQLite，不要求额外 Python 包。",
        "客户端使用 HTML CSS JavaScript，目标浏览器为当前稳定版 Chrome Edge Firefox。",
        "默认监听 127.0.0.1:8000；公开发布时必须增加反向代理 HTTPS 和持久化数据目录。",
        "默认数据位于系统临时目录；正式部署必须通过 ACADEMIC_LOAD_RADAR_DATA 指向持久化目录。",
    ])


def add_quality_sections(doc):
    doc.add_heading("8 非功能需求", level=1)
    nfr = [
        ["NFR-01", "性能", "高", "500 条任务内，本地批量分析 95% 响应不超过 1 秒；首次页面加载不超过 2 秒", "500 条固定数据连续运行 20 次统计 P95"],
        ["NFR-02", "易用性", "高", "首次用户 3 分钟内完成容量设置和首条任务录入，成功率至少 90%", "至少 5 名目标用户任务测试"],
        ["NFR-03", "可解释性", "高", "每个活动任务展示四个关键数值和原因，不仅显示颜色", "逐项界面检查"],
        ["NFR-04", "数据完整性", "高", "非法输入不得写入；批量导入错误定位到行", "边界值与事务测试"],
        ["NFR-05", "可靠性", "中", "正常重启数据不丢失；写入失败不破坏既有数据库", "重启测试与故障注入"],
        ["NFR-06", "兼容性", "中", "三种主流浏览器可完成核心流程；390px 与 1280px 无关键遮挡", "浏览器矩阵手工测试"],
        ["NFR-07", "隐私", "高", "本地模式不上传任务内容；不加载第三方跟踪脚本", "网络请求与源码检查"],
        ["NFR-08", "可维护性", "中", "算法与 HTTP 层分离；核心规则有自动化测试；需求可映射测试", "代码审查与追踪矩阵"],
        ["NFR-09", "安全", "中", "公开部署使用 HTTPS 限制请求体并防目录穿越；本地仅绑定回环地址", "配置审查与安全检查"],
        ["NFR-10", "精度", "高", "工时保留两位小数；固定输入与公式误差不超过 0.01 小时", "公式对照单元测试"],
    ]
    add_table(doc, ["编号", "类别", "优先级", "量化要求", "验证方法"], nfr, [0.7, 0.8, 0.7, 3.2, 1.35], font_size=7.9)
    doc.add_heading("9 故障模式与处理", level=1)
    failures = [
        ["F-01", "非法日期或数值", "计算失真", "返回 400 说明字段 不写库", "已实现核心校验"],
        ["F-02", "截止日期已过", "无法表达逾期", "直接标高风险并提示重新安排", "已实现"],
        ["F-03", "可用容量为 0", "除零或低估风险", "保护分母并解释无可用时间", "已实现"],
        ["F-04", "历史样本不足", "过早个性化", "H=1.0 少于 5 样本不调缓冲", "已实现"],
        ["F-05", "CSV 部分行错误", "静默丢行", "有效行保存 错误返回行号和原因", "已实现"],
        ["F-06", "重复导入 CSV", "样本重复", "计算记录指纹并提示重复", "待实现"],
        ["F-07", "不存在任务编号", "界面伪成功", "读取 修改 删除均返回 404", "已实现并需接口测试"],
        ["F-08", "数据库不可写或损坏", "数据丢失或崩溃", "事务回滚 返回 500 提供恢复说明", "待实现"],
        ["F-09", "主观进度偏差", "预测波动", "进度权重上限 75% 保留历史信号", "已实现"],
        ["F-10", "系统时间不正确", "分类和天数错误", "显示当前日期 使用本机时区并提示校时", "部分实现"],
    ]
    add_table(doc, ["编号", "故障", "风险", "规定处理", "现状"], failures, [0.65, 1.4, 1.8, 2.45, 0.9], font_size=8.1)


def add_trace_and_collaboration(doc):
    doc.add_heading("10 验收测试与追溯", level=1)
    tests = [
        ["TC-01", "FR-01 FR-12", "有效新增与非法输入", "接口加数据库检查", "计划补充"],
        ["TC-02", "FR-03", "历史任务分类并计入统计", "test_historical_task_is_classified_and_counted", "已通过"],
        ["TC-03", "FR-02 FR-05", "进行中任务按剩余量计算", "test_ongoing_task_uses_remaining_workload", "已通过"],
        ["TC-04", "FR-04", "缓冲影响容量", "test_capacity_respects_buffer", "已通过"],
        ["TC-05", "FR-05", "进度与历史效率融合", "test_progress_and_history_predict_remaining_time", "已通过"],
        ["TC-06", "FR-06", "多个任务共享容量", "test_cumulative_tasks_share_the_same_capacity", "已通过"],
        ["TC-07", "FR-06", "超载任务为高风险", "test_overloaded_task_is_high_risk", "已通过"],
        ["TC-08", "FR-07", "批量处理返回组合摘要", "test_batch_process_returns_portfolio_summary", "已通过"],
        ["TC-09", "FR-08", "完成任务剩余为零", "test_completed_task_is_low_risk", "已通过"],
        ["TC-10", "FR-02", "进度更新持久化并重新预测", "test_progress_update_changes_remaining_time_prediction", "已通过"],
        ["TC-11", "FR-05", "可信度使用进度和历史样本", "test_prediction_confidence_uses_observed_progress_and_history", "已通过"],
        ["TC-12", "FR-07", "EDF 同截止日期按优先级排序并报告缺口", "test_edf_schedule_prioritizes_higher_priority_on_same_deadline", "已通过"],
        ["TC-13", "FR-05", "进度与投入矛盾时降低可信度并预警", "test_inconsistent_progress_lowers_confidence_and_warns", "已通过"],
        ["TC-14", "FR-07", "大组合自适应排程窗口", "test_large_portfolio_expands_schedule_horizon", "已通过"],
        ["TC-15", "FR-09", "样本阈值和建议边界", "新增 insights 单元测试", "待实现"],
        ["TC-16", "FR-10", "CSV 部分成功与错误行", "新增导入接口测试", "待实现"],
        ["TC-17", "NFR-01", "500 条任务 P95", "性能脚本 20 次；以最新测试记录为准", "已通过"],
        ["TC-18", "NFR-02", "3 分钟首次任务", "5 人可用性测试", "待执行"],
        ["TC-19", "NFR-06", "浏览器和响应式", "三浏览器两视口矩阵", "待执行"],
        ["TC-20", "NFR-07", "无第三方上传", "开发者工具网络检查", "代码已核对 待手测"],
    ]
    add_table(doc, ["测试", "需求", "验证点", "证据或方法", "状态"], tests, [0.65, 1.0, 2.2, 2.2, 0.85], font_size=8.0)
    doc.add_heading("10.1 来源追溯", level=2)
    sources = [
        ["C01", "课程实验要求", "背景 场景 功能 非功能 优先级 验收 边界 AI记录 人工核实", "全局"],
        ["U01", "详细展开第三个想法并开始初步框架", "确定学业负荷雷达", "FR-01"],
        ["U02", "设置跟踪模式 调整缓冲 关注 deadline 效应", "跟踪和缓冲建议", "FR-04 FR-08 FR-09"],
        ["U03", "补录此前已开始与已完成任务", "历史数据补录", "FR-03 FR-10"],
        ["U04", "考虑其他任务并预测剩余时间", "组合风险和剩余预测", "FR-02 FR-05 FR-06"],
        ["U05", "批量处理已填写任务而非批量导入", "批量处理语义", "FR-07"],
        ["I01", "当前本地原型源码", "字段 接口 算法和现状", "FR-01 至 FR-12"],
        ["T01", "2026-10-08 本地单元测试", "13 项核心规则测试通过，包含进度更新 可信度 预警和 EDF", "FR-02 FR-05 FR-06 FR-07"],
        ["T02", "2026-10-08 LibreOffice 渲染与性能基准", "需求文档 27 页逐页检查无裁切；500 条任务 20 次运行 P95=334.842 ms", "NFR-01 NFR-03 NFR-08"],
        ["A01", "AI 提出的任务类型难度模型", "暂不纳入", "范围外"],
    ]
    add_table(doc, ["来源", "内容", "形成的结论", "关联需求"], sources, [0.65, 2.75, 2.35, 1.0], font_size=8.0)
    doc.add_heading("11 AI 协作记录", level=1)
    add_body(doc, "以下记录保留了本次需求形成过程中的主要提示词和决策。表中提示词来自实际对话，AI 产出均经过学生继续提问 修正或取舍；报告没有直接提交最初的 AI 原始输出。")
    add_body(doc, "为保证协作记录可核对，以下保留关键轮次的提示词原文节选：‘详细展开第三个想法’；‘开始构建一个初步框架’；‘缓冲比例是什么’；‘可以设置跟踪模式，观察近一段时间任务按时/提前完成情况，调整接下来缓冲比例，同时可以关注 deadline 前效率提升效应’；‘之后可以加入预设之前已经开始与完成任务的功能’；‘很致命的一个问题就是没有考虑其他任务’；‘新增批量处理功能’；‘我指的是批量处理已填写的任务，不是批量导入任务’；‘继续优化界面，增加合适算法’。完整对话由提交者保留，表格记录每轮的取舍结果。")
    rounds = [
        ["1", "分析文件结构 总结 docx 需要完成什么", "提取阶段和交付物", "采纳框架 本次重构", "任务清单"],
        ["2", "扩散思想 给出几个思路", "给出选题方向", "修改后采纳负荷风险方向", "确定选题"],
        ["3", "展开第三个想法并构建框架", "提出工时 容量 风险原型", "采纳核心 删除过早复杂化功能", "形成 MVP"],
        ["4", "缓冲比例是什么", "解释预留时间", "采纳并量化为容量扣减", "FR-04"],
        ["5", "跟踪按时提前 调整缓冲 关注 deadline 效应", "提出滚动窗口和效率指标", "修改：截止效应只解释", "FR-08 FR-09"],
        ["6", "加入此前已开始与完成任务", "提出两类补录", "采纳并区分字段和用途", "FR-02 FR-03"],
        ["7", "目前算法和任务书差距", "解释算法并列差距", "采纳可解释算法 拒绝虚报完成", "差距管理"],
        ["8", "生成 CSV 并设计进行中导入", "设计两类 CSV", "采纳字段 增加精度和压力", "FR-10"],
        ["9", "没有考虑其他任务 改正并预测剩余时间", "提出累计容量和融合估算", "采纳全部较早任务累计", "FR-05 FR-06"],
        ["10", "以后可用大模型分析任务难度", "提出按类型建模", "暂缓：样本 隐私 可验证性不足", "范围外"],
        ["11", "新增批量处理功能", "AI 初次理解为增强导入", "否决", "发现歧义"],
        ["12", "批量处理已填写任务 不是批量导入", "改为全部任务统一重算", "采纳人工纠正", "FR-07"],
        ["13", "按个人判分要求撰写当前项目文档", "按评分表审查旧文档", "结构性重写", "本版本"],
        ["14", "继续优化界面，增加合适算法", "提出界面层级优化和 EDF 排程 可信度算法", "采纳并实现；补充为 1.1 需求基线", "FR-02 FR-05 FR-06 FR-07"],
        ["15", "分析结果是否合理并按建议修改", "指出样例过期、进度矛盾和固定排程窗口风险", "采纳：加入预警、自适应窗口和样例解释", "FR-05 FR-07"],
    ]
    add_table(doc, ["轮次", "用户提示词摘要", "AI 产出", "采纳 修改或否决", "影响"], rounds, [0.45, 2.1, 1.55, 2.0, 0.65], font_size=7.5)
    doc.add_heading("11.1 AI 内容取舍总结", level=2)
    add_table(doc, ["类别", "内容", "理由"], [
        ["采纳", "累计负荷 进度与历史融合 跟踪模式 历史补录", "回应明确问题 能用当前技术实现并验证"],
        ["修改", "截止前效率提升与缓冲调整", "短期赶工效率不等于长期可持续容量"],
        ["修改", "批量处理", "学生纠正为处理已有任务 不是导入"],
        ["否决", "直接用大模型判定任务难度", "缺少样本 评价标准和隐私方案"],
        ["否决", "把原型能力全部标为已完成", "性能 兼容性 故障恢复尚未测试"],
    ], [0.75, 3.0, 2.95], font_size=8.5)


def add_verification_and_close(doc):
    doc.add_heading("12 人工核实说明", level=1)
    add_body(doc, "人工核实分为需求决策复核和技术证据核查。需求决策以学生在对话中的明确补充或纠正为依据；技术事实以本地代码 测试和数据格式逐项核对。性能基准已在本机 LibreOffice 配置和本地 Python 环境中完成；浏览器和可用性测试仍明确标记为待核实，不能在提交时写成已通过。")
    checks = [
        ["HV-01", "选题与核心问题", "学生连续要求展开想法并开始框架", "采用学业负荷雷达", "已人工确认"],
        ["HV-02", "缓冲与跟踪", "学生提出按时 提前和 deadline 效应", "跟踪窗口和安全缓冲", "已人工确认"],
        ["HV-03", "历史和进行中数据", "学生要求补录既有任务", "两类数据均需要", "已人工确认"],
        ["HV-04", "组合风险", "学生指出遗漏其他任务", "累计较早截止活动任务", "已人工确认"],
        ["HV-05", "批量处理语义", "学生否决批量导入解释", "处理已经填写的任务", "已人工确认"],
        ["HV-06", "算法可运行性", "核对 risk.py 并运行 13 项测试", "核心计算、进度更新、可信度、矛盾预警、自适应窗口和 EDF 测试全部通过", "有自动化证据"],
        ["HV-07", "输入输出格式", "核对 main.py 和示例 CSV", "字段和枚举一致", "有代码证据"],
        ["HV-08", "性能指标", "500 条固定任务连续运行 20 次；LibreOffice 渲染 27 页并逐页检查", "P95=334.842 ms，小于 1 秒；页面无裁切或重叠", "已核实"],
        ["HV-09", "浏览器与易用性", "尚未完成三浏览器和 5 人测试", "不得声明指标已通过", "待人工执行"],
        ["HV-10", "课程素材模板", "素材链接 2026-09-23 返回 404", "无法确认额外模板", "待链接恢复"],
    ]
    add_table(doc, ["编号", "关键结论", "核实方式", "结果", "状态"], checks, [0.65, 1.55, 2.25, 1.75, 0.75], font_size=7.8)
    doc.add_heading("12.1 提交前个人核实清单", level=2)
    add_bullets(doc, [
        "由学生本人复算一个剩余工时示例和一个组合超载示例，记录输入与结果。",
        "运行全部 11 项自动化测试并保存通过截图或终端记录。",
        "使用 Chrome Edge Firefox 完成新增 进行中录入 批量处理 完成和删除流程。",
        "邀请至少 5 名目标用户完成首次使用任务，记录时间 失败点和修改结果。",
        "课程素材链接恢复后核对指定封面 命名或模板。",
        "填写个人姓名 学号 班级，并以本人语言完成最后一次通读和签认。",
    ])
    doc.add_heading("13 需求基线与待办", level=1)
    add_body(doc, "本版本把学业负荷雷达的核心需求确定为可解释的剩余工时预测、多任务累计容量分析和 EDF 行动顺序建议。普通任务 进行中任务进度更新 历史任务 容量设置 批量处理 预测可信度 进度矛盾预警 自适应排程窗口 近期统计和性能基准已经有原型证据；重复导入检测 数据库故障处理 跨浏览器测试和可用性测试仍是明确差距。")
    add_table(doc, ["顺序", "待办", "对应评分点", "完成判据"], [
        ["1", "补充接口边界和 insights 测试", "正确性 可验证性", "新增测试全部通过"],
        ["2", "增加重复 CSV 检测和数据库异常处理", "完备性 可靠性", "故障用例可复现并通过"],
        ["3", "执行浏览器矩阵", "非功能可验证性", "三浏览器两视口核心流程通过"],
        ["4", "开展 5 人可用性测试并修订需求", "人工核实", "形成原始记录 修改说明和结论"],
    ], [0.55, 2.75, 1.7, 1.7], font_size=8.5)
    add_body(doc, "需求变更时必须记录变更日期 提出者 原需求编号 修改理由 受影响的验收测试和实现状态。未经人工确认的 AI 建议只能进入候选清单，不能直接替换需求基线。")


def build():
    doc = setup_document()
    add_front_matter(doc)
    add_context_sections(doc)
    add_requirements(doc)
    add_rules_data_interfaces(doc)
    add_quality_sections(doc)
    add_trace_and_collaboration(doc)
    add_verification_and_close(doc)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    doc.save(OUTPUT)
    print(OUTPUT)


if __name__ == "__main__":
    build()
