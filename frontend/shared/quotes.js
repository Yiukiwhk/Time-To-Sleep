/**
 * 鼓励坚持的引文（名言）库。
 *
 * 为什么单独一个模块、而且自带中英两版文案：引文属于「语料」而不是界面文案，
 * 扔进 i18n 字典会让那个文件迅速膨胀（每条要两行，30 条就是 60 行），还会让
 * check_i18n_keys 的「前端用了多少键」失去意义。这里每条自带 {zh, en, by}，
 * 渲染时按当前语言取一个字段。
 *
 * 用法：quoteOfDay(key) —— key 通常是「生成日期」，同一天永远返回同一条，
 * 刷新不会跳来跳去（这点很重要：一条每次刷新都变的"鸡汤"比没有更烦）。
 */

const QUOTES = [
  {
    zh: "不积跬步，无以至千里；不积小流，无以成江海。",
    en: "No long journey without small steps; no great river without small streams.",
    by: { zh: "荀子《劝学》", en: "Xunzi, Encouraging Learning" },
  },
  {
    zh: "锲而舍之，朽木不折；锲而不舍，金石可镂。",
    en: "Give up halfway and even rotten wood stays whole; keep carving and even metal can be pierced.",
    by: { zh: "荀子《劝学》", en: "Xunzi, Encouraging Learning" },
  },
  {
    zh: "千里之行，始于足下。",
    en: "A journey of a thousand miles begins with a single step.",
    by: { zh: "《老子》", en: "Laozi, Tao Te Ching" },
  },
  {
    zh: "宝剑锋从磨砺出，梅花香自苦寒来。",
    en: "A blade is sharpened by the whetstone; plum blossom smells sweet because of the bitter cold.",
    by: { zh: "《警世贤文》", en: "Jingshi Xianwen" },
  },
  {
    zh: "我们重复做的事决定了我们是谁。卓越不是一个行为，而是一种习惯。",
    en: "We are what we repeatedly do. Excellence, then, is not an act, but a habit.",
    by: { zh: "威尔·杜兰特", en: "Will Durant" },
  },
  {
    zh: "伟大的作品不是靠力量，而是靠坚持完成的。",
    en: "Great works are performed not by strength but by perseverance.",
    by: { zh: "塞缪尔·约翰逊", en: "Samuel Johnson" },
  },
  {
    zh: "精力与坚持能攻克一切。",
    en: "Energy and persistence conquer all things.",
    by: { zh: "本杰明·富兰克林", en: "Benjamin Franklin" },
  },
  {
    zh: "生活对谁都不容易。但那又怎样？我们必须有毅力。",
    en: "Life is not easy for any of us. But what of that? We must have perseverance.",
    by: { zh: "居里夫人", en: "Marie Curie" },
  },
  {
    zh: "你无法控制外界发生什么，但你能控制自己的反应；由此你就有了力量。",
    en: "You have power over your mind — not outside events. Realize this, and you will find strength.",
    by: { zh: "马可·奥勒留", en: "Marcus Aurelius" },
  },
  {
    zh: "问题不在于我们拥有的时间太短，而在于我们浪费了太多。",
    en: "It is not that we have a short time to live, but that we waste a lot of it.",
    by: { zh: "塞内卡", en: "Seneca" },
  },
  {
    zh: "走得慢不要紧，要紧的是不停下来。",
    en: "It does not matter how slowly you go, so long as you do not stop.",
    by: { zh: "相传为孔子语", en: "attributed to Confucius" },
  },
  {
    zh: "冰冻三尺，非一日之寒。",
    en: "Three feet of ice does not form in a single day.",
    by: { zh: "《增广贤文》", en: "Zengguang Xianwen" },
  },
];

function pickLanguage() {
  try {
    return window.localStorage.getItem("ssp.lang") === "en" ? "en" : "zh";
  } catch (error) {
    return "zh";
  }
}

/** 同一天永远同一条：按 key（通常是日期字符串）做一次稳定的散列取模 */
function quoteOfDay(key) {
  if (!QUOTES.length) return null;
  const text = String(key || "");
  let hash = 0;
  for (let index = 0; index < text.length; index += 1) {
    hash = (hash * 31 + text.charCodeAt(index)) % 1000000007;
  }
  const entry = QUOTES[hash % QUOTES.length];
  const lang = pickLanguage();
  return {
    text: entry[lang] || entry.zh,
    by: (entry.by && (entry.by[lang] || entry.by.zh)) || "",
  };
}

export { QUOTES, quoteOfDay };
