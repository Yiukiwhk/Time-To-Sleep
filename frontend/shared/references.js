// 参考文献数据（用户端"参考文献"弹窗使用）。
//
// 说明：这里使用的是逐条校对过的正式引文——作者、卷期、页码与 DOI 均可正常打开，
// 与 docs/REFERENCES.md 里的条目一致。
export const CITATION_NOTE = {
  zh: "以上引文已按 Crossref / PubMed 逐条校对：作者、卷期、页码与 DOI 均可正常打开。",
  en: "Every citation above was checked against Crossref / PubMed: authors, volume, pages and DOI all resolve.",
};

// 每条文献附带"本应用如何使用它"的说明，避免把弱证据包装成强结论。
export const REFERENCES = [
  {
    id: "czeisler1981",
    authors: "Czeisler CA, Richardson GS, Coleman RM, Zimmerman JC, Moore-Ede MC, Dement WC, Weitzman ED",
    year: 1981,
    title: "Chronotherapy: resetting the circadian clocks of patients with delayed sleep phase insomnia",
    journal: "Sleep",
    volume: "4(1)",
    pages: "1–21",
    doi: "10.1093/sleep/4.1.1",
    url: "https://doi.org/10.1093/sleep/4.1.1",
    pmid: "7232967",
    summary: {
      zh: "5 例睡眠时相延迟患者在无药物条件下用“逐日移动睡眠时段”的方法重置生物钟：入睡时间平均从 4:50 提前到 0:20，起床时间从 13:00 提前到 7:55，随访平均 260 天保持稳定，睡眠效率没有下降。论文没有给出“每天最多移动多少”的安全上限，本应用的步长上限不取自本文。",
      en: "Five delayed-sleep-phase patients reset their clock by shifting the sleep episode day by day: sleep onset moved from 4:50 to 0:20 and wake from 13:00 to 7:55 on average, stable over a mean 260-day follow-up with no loss of sleep efficiency. The paper gives no maximum safe daily shift, so this app does not take its step caps from it.",
    },
  },
  {
    id: "eastman2005",
    authors: "Eastman CI, Gazda CJ, Burgess HJ, Crowley SJ, Fogg LF",
    year: 2005,
    title: "Advancing Circadian Rhythms Before Eastward Flight: A Strategy to Prevent or Reduce Jet Lag",
    journal: "Sleep",
    volume: "28(1)",
    pages: "33–44",
    doi: "10.1093/sleep/28.1.33",
    url: "https://doi.org/10.1093/sleep/28.1.33",
    pmid: "15700719",
    pmcid: "PMC1249488",
    summary: {
      zh: "26 名受试者在出发前 3 天把起床时间每天提前 1 小时（11 人）或 2 小时（15 人），并配合晨间强光：DLMO 平均前移 1.5 小时（≈30 分钟/天）与 1.8 小时（≈36 分钟/天）。2 小时/天的那组在第 3 天就出现“上床时间早于 DLMO、起床早于体温最低点”，入睡潜伏期变长、睡眠效率下降，作者因此推荐每天 1 小时。这是本应用“前移每天上限 45 分钟”的主要依据。",
      en: "Twenty-six participants advanced wake time by 1 h/day (n=11) or 2 h/day (n=15) for 3 days with morning bright light: DLMO advanced 1.5 h (~30 min/day) and 1.8 h (~36 min/day). The 2 h/day group was already misaligned by day 3 (bedtime before DLMO, wake before the temperature minimum) with longer sleep-onset latency and lower sleep efficiency, so the authors recommend 1 h/day. This is the main basis for the app's 45-minute advance cap.",
    },
  },
  {
    id: "misiunaite2020",
    authors: "Misiunaite I, Eastman CI, Crowley SJ",
    year: 2020,
    title: "Circadian Phase Advances in Response to Weekend Morning Light in Adolescents With Short Sleep and Late Bedtimes on School Nights",
    journal: "Frontiers in Neuroscience",
    volume: "14",
    pages: "99",
    doi: "10.3389/fnins.2020.00099",
    url: "https://doi.org/10.3389/fnins.2020.00099",
    pmid: "32116532",
    pmcid: "PMC7029701",
    summary: {
      zh: "青少年周末两个早晨的晨光实验：2.5 小时强光组 DLMO 前移 1.0 小时（约 30 分钟/天），而房间光或 1.5 小时光只有 0.6 小时。说明“早起 + 起床后尽快接触明亮光”是前移相位最有效的杠杆，量级同样是每天半小时左右——因此本应用把光照提示与前移日绑定。",
      en: "In adolescents, two weekend mornings of bright light advanced DLMO by 1.0 h (~30 min/day) with 2.5 h of light, versus only 0.6 h with room light or 1.5 h of light. Waking earlier plus bright light right after waking is the strongest advance lever, again at a rate of about half an hour per day — which is why light tips are attached to advance days.",
    },
  },
  {
    id: "ravi2025",
    authors: "Ravi N, Gaur A, Varatharajan S, Earl EH, Kalpana M, Kamble P, Daulatabad V, Singhal A",
    year: 2025,
    title: "Sleep and chrononutrition: a narrative review",
    journal: "Sleep and Biological Rhythms",
    volume: "23(4)",
    pages: "411–417",
    doi: "10.1007/s41105-025-00607-7",
    url: "https://doi.org/10.1007/s41105-025-00607-7",
    pmid: "40988908",
    pmcid: "PMC12450850",
    summary: {
      zh: "饮食与睡眠的叙事综述（证据强度偏弱、以相关性为主）：≥3 餐/天与更好的睡眠质量相关，不吃早餐与入睡推迟稳定相关，晚餐距离睡前 ≥2 小时时未见睡眠质量恶化。但它没有给出任何“起床后 X 分钟进食”的钟点窗口，也没有证据表明进餐时间能移动昼夜相位。因此本应用把三餐当作“与作息同步的节律锚点”，窗口按当天起床/入睡成比例给出，并明确提示三餐不是移动生物钟的手段。",
      en: "A narrative review of diet and sleep (weak, mostly correlational evidence): ≥3 meals/day is associated with better sleep quality, skipping breakfast with a later bedtime, and no deterioration when dinner is ≥2 h before bedtime. It provides no clock-time windows such as \"eat within X minutes of waking\", and no evidence that meal timing shifts circadian phase. The app therefore treats meals as rhythm anchors proportioned to the day's wake/sleep times, and says plainly that meals do not move the clock.",
    },
  },
];

export function referenceList() {
  return REFERENCES.slice();
}
