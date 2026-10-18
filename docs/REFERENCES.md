# 参考文献与算法依据

本文件记录「作息建议」算法（engine = `adaptive-v2`）所依据的文献，
以及每条设计常量与证据之间的对应关系。网站用户端页脚的「参考文献」按钮展示的
内容来自 [frontend/shared/references.js](../frontend/shared/references.js)，与本文件保持一致。

## 一、四篇文献

### 1. Czeisler CA, Richardson GS, Coleman RM, Zimmerman JC, Moore-Ede MC, Dement WC, Weitzman ED.
*Chronotherapy: resetting the circadian clocks of patients with delayed sleep phase insomnia.*
Sleep 1981;4(1):1–21. doi:10.1093/sleep/4.1.1 — PMID 7232967（非开放获取）

5 例睡眠时相延迟患者，采用连续数日逐步后移睡眠时段的「时间疗法」。入睡时间平均
由 4:50 前移到 0:20（4.5 小时），起床时间由 13:00 前移到 7:55（约 5.1 小时），
p<0.001，睡眠效率未见下降；随访平均 260 天（42–910 天）效果维持，5 例全部摆脱
催眠药依赖。摘要未给出每日后移量的上限，全文需付费，故**本算法不引用其速率**，
仅作为「相位可以大幅前移且不牺牲睡眠效率」的旁证。

### 2. Eastman CI, Gazda CJ, Burgess HJ, Crowley SJ, Fogg LF.
*Advancing Circadian Rhythms Before Eastward Flight: A Strategy to Prevent or Reduce Jet Lag.*
Sleep 2005;28(1):33–44. doi:10.1093/sleep/28.1.33 — PMID 15700719, PMC1249488

26 名完成者（22–36 岁），实验室 3 天，起床时间每天前移 1 小时（n=11）或 2 小时（n=15），
配合起床后 3.5 小时的间歇强光（4×30 分钟，约 5075 lux）。以 DLMO 衡量：
**1 小时/天组平均前移 1.5 小时（≈30 分钟/天）；2 小时/天组 1.8 小时（≈36 分钟/天）**，
两组差异不显著。2 小时/天组在第 3 天出现入睡时间早于 DLMO、起床早于体温最低点的
相位错位，入睡潜伏期延长、睡眠效率下降，作者据此**推荐每天前移 1 小时而不是 2 小时**。

### 3. Misiunaite I, Eastman CI, Crowley SJ.
*Circadian Phase Advances in Response to Weekend Morning Light in Adolescents With Short Sleep and Late Bedtimes on School Nights.*
Front Neurosci 2020;14:99. doi:10.3389/fnins.2020.00099 — PMID 32116532, PMC7029701

37 名青少年，周末两个早晨在起床后 5 分钟内接受光照：房间光（约 21 lux）DLMO 前移
0.6 h；1.5 小时强光（约 5961 lux）0.6 h（与房间光无差异）；**2.5 小时强光（约 5910 lux）
前移 1.0 h**（≈30 分钟/天）。说明「晨光 + 早起」是主要杠杆，且需要足够的光照时长。

### 4. Ravi N, Gaur A, Varatharajan S, Earl EH, Kalpana M, Kamble P, Daulatabad V, Singhal A.
*Sleep and chrononutrition: a narrative review.*
Sleep Biol Rhythms 2025;23(4):411–417. doi:10.1007/s41105-025-00607-7 — PMID 40988908, PMC12450850

叙事综述（截至 2025-02-20）。可支持的弱关联结论：≥3 餐/天与更好的睡眠质量相关；
不吃早餐与入睡推迟稳定相关；晚餐越靠近睡前，入睡越晚（约 30 分钟）、睡眠更短；
**睡前 ≥2 小时进食未见睡眠质量恶化**。该综述**没有给出任何钟点窗口**（没有「起床后
X 分钟吃早餐」这类建议），也**没有任何证据表明进餐时间能移动昼夜相位**（无 DLMO /
CBTmin 指标）。因此本算法把三餐当作「可执行的节律锚点」，而不是相位杠杆。

## 二、设计常量与证据的对应

| 常量（backend/app/services/advice.py） | 取值 | 依据 |
| --- | --- | --- |
| `STEP_CAP_ADVANCE` / `STEP_CAP_DELAY`（每日建议步长） | 60 分钟 | **提示阈值，不截断曲线**。文献：Eastman 2005 里 1 小时/天 ≈ 30 分钟/天的有效前移、2 小时/天 ≈ 36 分钟/天且睡眠效率下降；跨时区估计西行（后移）92 分钟/天 vs 东行（前移）57 分钟/天。取 60 是产品侧「1 小时最适宜、2 小时也没问题」的折中，两个方向取同一个数（文献没有直接给出「45/90」这种不对称值） |
| `STEP_SHAPE_ALPHA` / `STEP_SHAPE_POWER`（曲线形状） | 0.6 / 1.0 | 先快后缓：`s_N/s_1 = 1/(1+α) ≈ 0.62`、`s_1/日均 ≈ 1.22`，与天数无关，保证末日仍在移动（旧的分段表 60/45/30/15 会走到头，把差额全压到末日）。曲线形状本身是工程选择，不是文献结论 |
| `STEP_CAP_HARD`（单日输入上限） | 120 分钟 | 产品侧初始要求（「2 小时没问题」），`fast` 档的可行判定与输入校验用它 |
| `BREAKFAST_AFTER_WAKE` | 起床后 15–30 分钟 | 综述支持「吃早餐优于不吃」，但**没有给出具体间隔**；取起床后 15 分钟为最佳时间点，属于温和的工程默认值 |
| 午餐起点（取早餐与晚餐中点，再提前 `MEAL_ADVANCE_MIN`） | 夹取范围 [起床+120, 晚餐−240] 分钟 | 同上，按「三餐均匀分布」的常识区间给出；早先锚在「起床+180 分钟」，白天越长午餐与晚餐隔得越远（06:00 起、01:00 睡的人间隔 12.5 小时），改为取中点后收敛到 7.6 小时；再整体提前 1 小时是用户需求（给饭后留出活动时间） |
| `LUNCH_AFTER_WAKE` | 窗口长 120 分钟 | 只用于午餐窗口长度（`window_end`），不再决定窗口起点 |
| `DINNER_BEFORE_SLEEP` | 睡前 120–210 分钟（最佳 210） | 综述里唯一的硬区间证据：睡前 ≥2 小时进食未见睡眠质量恶化；最佳取 3.5 小时，实际起点再减 `MEAL_ADVANCE_MIN = 60`（共睡前 270 分钟） |
| `MEAL_ADVANCE_MIN` | 午餐与晚餐整体提前 60 分钟 | 用户需求：现有算法整体提前 1 小时；无文献依据（属工程偏好），故不改变 `DINNER_BEFORE_SLEEP` 的证据口径，只作为叠加项 |
| `NORMAL_WAKE_UNTIL` / `LUNCH_NORMAL_WINDOW` | 起床 ≤ 15:00 → 午餐就近靠拢进 11:00–15:00 | 用户需求（社会时钟参考区间）；只在作息正常时生效，越界即跳过，避免把极端作息也硬塞进常规饭点 |
| `NORMAL_SLEEP_UNTIL` / `DINNER_NORMAL_WINDOW` | 入睡不晚于次日 06:00（或当天 17:00 之后）→ 晚餐就近靠拢进 17:00–22:00 | 同上；06:00–17:00 之间入睡视为白天补觉，此时不套用窗口 |
| `LUNCH_ADVANCE_LADDER` | 60 / 90 / 120 分钟 | 用户需求：早餐没吃则午餐适当前移；阶梯上限 2 小时，且午餐不早于起床后 120 分钟 |
| `DEVIATION_OK / DEVIATION_ASK` | 30 / 90 分钟 | 用户需求：偏离 ≤30 分钟不处理、30–90 分钟静默重算、>90 分钟询问是否顺延目标日期 |
| `SHORT_PLAN_DAILY_THRESHOLD` | 60 分钟/天 | 高于证据支持的 30–45 分钟/天即提示「可能导致睡眠效率下降」（措辞温和，可「不再提示」） |
| `MEAL_HISTORY_DAYS` | 7 天 | 连续未吃早餐的判定窗口，避免单日波动就触发前移 |
| `IDEAL_SLEEP / IDEAL_WAKE / IDEAL_DURATION` | 23:00 / 07:00 / 480 分钟 | 未设置自定义目标且历史不足时的系统默认目标 |

## 三、全文存档

四篇文献的正文/摘要纯文本（调研阶段抓取）位于工作区 `papers/` 目录：
`eastman2005_sleep.txt`、`misiunaite2020.txt`、`chrononutrition2025.txt`、
`crswd_treatment_review.txt`（CRSWD 治疗综述，作为补充背景）。
