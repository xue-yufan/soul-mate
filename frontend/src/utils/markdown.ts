import DOMPurify from "dompurify";
// 只带常用语言：全量入口（highlight.js）会把 190+ 种语言全打进包里，
// 渲染层产物会从 ~250KB 涨到 ~1.9MB。common 覆盖 36 种，够本项目用。
import hljs from "highlight.js/lib/common";
import { Marked } from "marked";
import { markedHighlight } from "marked-highlight";

// ---------- marked 实例 ----------

const marked = new Marked(
    markedHighlight({
        langPrefix: "hljs language-",
        highlight(code: string, lang: string): string {
            const language = lang && hljs.getLanguage(lang) ? lang : "plaintext";
            try {
                return hljs.highlight(code, { language }).value;
            } catch {
                return code;
            }
        },
    }),
);

marked.setOptions({
    gfm: true, // GitHub Flavored Markdown
    breaks: true, // 单个换行也换行
});

// ---------- 净化配置 ----------

const PURIFY_CONFIG = {
    ALLOWED_TAGS: [
        "p",
        "br",
        "hr",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "strong",
        "em",
        "del",
        "code",
        "pre",
        "blockquote",
        "ul",
        "ol",
        "li",
        "a",
        "img",
        "table",
        "thead",
        "tbody",
        "tr",
        "th",
        "td",
        "span",
        "div",
    ],
    ALLOWED_ATTR: [
        "href",
        "title",
        "target",
        "rel",
        "src",
        "alt",
        "width",
        "height",
        "class",
        "id",
    ],
    ALLOW_DATA_ATTR: false,
};

// ---------- 渲染 ----------

/**
 * 把 Markdown 渲染成净化过的 HTML。
 *
 * 输入空串返回空串。
 */
export function renderMarkdown(md: string): string {
    if (!md) {
        return "";
    }

    let html: string;
    try {
        html = marked.parse(md) as string;
    } catch (err) {
        console.warn("[markdown] 解析失败：", err);
        return escapeHtml(md);
    }

    // 净化
    return DOMPurify.sanitize(html, PURIFY_CONFIG) as unknown as string;
}

/**
 * 纯文本转义，作为解析失败时的兜底
 */
function escapeHtml(text: string): string {
    return text
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
}
