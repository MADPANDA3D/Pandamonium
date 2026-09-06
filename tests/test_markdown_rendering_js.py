"""Regression coverage for the browser markdown renderer."""

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_HAS_NODE = shutil.which("node") is not None


@pytest.fixture(scope="module")
def node_available():
    if not _HAS_NODE:
        pytest.skip("node binary not on PATH")


def _run_markdown_case(markdown: str, render_expr: str = "mod.mdToHtml(input)", with_katex: bool = False):
    script = textwrap.dedent(
        r"""
        import fs from 'node:fs';

        globalThis.window = { location: { origin: 'http://localhost' }, katex: null };
        if (__WITH_KATEX__) {
          // Minimal stand-in for the CDN katex global: wraps the source so tests
          // can assert what was (or wasn't) handed to KaTeX.
          const katexStub = {
            renderToString(src, opts) {
              const display = !!(opts && opts.displayMode);
              return `<span class="katex" data-display="${display}">${src}</span>`;
            },
          };
          globalThis.window.katex = katexStub;
          globalThis.katex = katexStub;
        }
        globalThis.document = {
          readyState: 'loading',
          addEventListener() {},
          createElement(tag) {
            if (tag !== 'template') throw new Error(`unsupported element: ${tag}`);
            return {
              _html: '',
              content: { querySelectorAll() { return []; } },
              set innerHTML(value) { this._html = value; },
              get innerHTML() { return this._html; },
            };
          },
        };
        globalThis.MutationObserver = class { observe() {} };

        let source = fs.readFileSync('./static/js/markdown.js', 'utf8');
        source = source.replace(
          /import uiModule from ['"]\.\/ui\.js['"];/,
          ''
        );
        source = source.replace(
          /import \{ splitTableRow \} from ['"]\.\/markdown\/tableRow\.js['"];/,
          `function splitTableRow(row) {
            return (row || '').replace(/^\\s*\\|/, '').replace(/\\|\\s*$/, '').split('|').map(c => c.trim());
          }`
        );
        // markdown.js imports the emoji-shortcode helpers relatively (issue #345),
        // which a data: URL module can't resolve. Inline the REAL helpers (minus
        // their export keywords) so the renderer's shortcode pass behaves exactly
        // as it does in the browser.
        const emojiSource = fs.readFileSync('./static/js/emojiShortcodes.js', 'utf8')
          .replace(/^export default .*$/m, '')
          .replace(/export const /g, 'const ')
          .replace(/export function /g, 'function ');
        source = source.replace(
          /import \{ replaceEmojiShortcodes, hasEmojiShortcode \} from ['"]\.\/emojiShortcodes\.js['"];/,
          () => emojiSource
        );
        source = source.replace(
          /var escapeHtml = uiModule\.esc;/,
          `var escapeHtml = (value) => String(value ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');`
        );

        const moduleUrl = 'data:text/javascript;base64,' + Buffer.from(source).toString('base64');
        const mod = await import(moduleUrl);
        const input = JSON.parse(process.argv[1]);
        console.log(JSON.stringify({ html: __RENDER_EXPR__ }));
        """
    ).replace("__RENDER_EXPR__", render_expr).replace(
        "__WITH_KATEX__", "true" if with_katex else "false"
    )
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script, json.dumps(markdown)],
        cwd=_REPO,
        capture_output=True,
        timeout=15,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed:\nSTDERR:\n{result.stderr}\nSTDOUT:\n{result.stdout}")
    return json.loads(result.stdout.splitlines()[-1])["html"]


def test_ordered_lists_render_as_one_unwrapped_ol(node_available):
    html = _run_markdown_case(
        "Before\n\n"
        "1. **Check against the home page** — that's the visual reference for how things should feel.\n"
        "2. **Open DevTools** and inspect the element — check fonts, colors, and spacing against this guide.\n"
        "3. **Flag it** — note the page, the section, what's wrong, and what CSS rule you suspect.\n"
        "4. **Small fixes** — if you know the fix (e.g. wrong CSS variable, wrong font), go ahead and change it in the CSS Module file.\n"
        "5. **Big changes** — Talk it through before making wide changes across many pages.\n\n"
        "After"
    )

    assert html.count("<ol>") == 1
    assert html.count("</ol>") == 1
    assert html.count("<li>") == 5
    assert "<ul>" not in html
    assert "<oli>" not in html
    assert "<uli>" not in html
    assert "<p><ol>" not in html
    assert "<p><li>" not in html
    assert "<p>Before</p>" in html
    assert "<p>After</p>" in html


def test_table_separator_row_not_rendered_as_data(node_available):
    html = _run_markdown_case("| A | B |\n|---|---|\n| 1 | 2 |")

    assert html.count("<tr>") == 2
    assert "<th" in html
    assert "<td" in html
    assert "---" not in html


def test_table_with_url_stays_a_table_and_restores_safe_link(node_available):
    html = _run_markdown_case(
        "| Service | Endpoint |\n"
        "|---|---|\n"
        "| Pandamonium | https://example.com/a/very/long/path |"
    )

    assert "<table" in html
    assert html.count("<tr>") == 2
    assert 'href="https://example.com/a/very/long/path"' in html
    assert "___ALLOWED_HTML_" not in html


def test_process_with_thinking_handles_gemma4_thought_channel(node_available):
    html = _run_markdown_case(
        "<|channel>thought\ninternal reasoning<channel|>Final answer.",
        "mod.processWithThinking(input)",
    )

    assert "thinking-section" in html
    assert "internal reasoning" in html
    assert "Final answer." in html
    assert "&lt;|channel&gt;" not in html
    assert "<|channel>" not in html


def test_process_with_thinking_strips_empty_gemma4_thought_channel(node_available):
    html = _run_markdown_case(
        "<|channel>thought\n<channel|>Final answer.",
        "mod.processWithThinking(input)",
    )

    assert "thinking-section" not in html
    assert "Final answer." in html
    assert "&lt;|channel&gt;" not in html
    assert "<|channel>" not in html


def test_process_with_thinking_unwraps_gemma4_response_channel(node_available):
    html = _run_markdown_case(
        "<|channel>thought\ninternal reasoning<channel|><|channel>response\nFinal answer.<channel|>",
        "mod.processWithThinking(input)",
    )

    assert "thinking-section" in html
    assert "internal reasoning" in html
    assert "Final answer." in html
    assert "&lt;|channel&gt;" not in html
    assert "<|channel>" not in html


def test_extract_thinking_blocks_handles_thought_tag(node_available):
    result = _run_markdown_case(
        "<thought>internal reasoning</thought>Final answer.",
        "mod.extractThinkingBlocks(input)",
    )

    assert result["thinkingBlocks"] == ["internal reasoning"]
    assert result["content"] == "Final answer."


def test_url_inside_inline_code_is_not_autolinked(node_available):
    # A URL inside a backtick span is preceded by a space, so the bare-URL
    # autolink used to wrap it in an <a> tag (then swap it for an
    # ___ALLOWED_HTML_ placeholder), corrupting the command shown to the user.
    html = _run_markdown_case("Run `$j = irm http://127.0.0.1:3000/x` to fetch.")

    assert "<code>$j = irm http://127.0.0.1:3000/x</code>" in html
    assert "___ALLOWED_HTML_" not in html
    assert "<a " not in html
    assert 'href="http://127.0.0.1:3000/x"' not in html


def test_url_outside_inline_code_is_still_autolinked(node_available):
    # Inline code must not disable autolinking for bare URLs elsewhere in the
    # same line.
    html = _run_markdown_case("Use `irm` then visit https://example.com/page now.")

    assert "<code>irm</code>" in html
    assert 'href="https://example.com/page"' in html


def test_bare_github_repository_link_is_branded_and_compact(node_available):
    html = _run_markdown_case("https://github.com/MADPANDA3D/Pandamonium")

    assert 'class="rich-link rich-link-github"' in html
    assert 'href="https://github.com/MADPANDA3D/Pandamonium"' in html
    assert 'target="_blank" rel="noopener noreferrer"' in html
    assert 'src="/static/icons/brands/github.svg"' in html
    assert '<span class="rich-link-label">MADPANDA3D/Pandamonium</span>' in html
    assert '>https://github.com/MADPANDA3D/Pandamonium</span>' not in html


def test_deep_github_link_keeps_its_path_context(node_available):
    url = "https://github.com/MADPANDA3D/Pandamonium/issues/833"
    html = _run_markdown_case(url)

    assert 'class="rich-link rich-link-github"' in html
    assert f'<span class="rich-link-label">{url}</span>' in html


def test_named_github_link_keeps_author_label(node_available):
    html = _run_markdown_case(
        "[Pandamonium source](https://github.com/MADPANDA3D/Pandamonium)"
    )

    assert 'class="rich-link rich-link-github"' in html
    assert '<span class="rich-link-label">Pandamonium source</span>' in html


@pytest.mark.parametrize(
    ("url", "service", "icon"),
    [
        ("https://www.instagram.com/p/abc123/", "instagram", "instagram.svg"),
        ("https://www.facebook.com/share/r/example", "facebook", "facebook.svg"),
    ],
)
def test_social_links_get_branded_icons_without_changing_destination(
    node_available, url, service, icon
):
    html = _run_markdown_case(url)

    assert f'class="rich-link rich-link-{service}"' in html
    assert f'href="{url}"' in html
    assert f'src="/static/icons/brands/{icon}"' in html
    assert 'target="_blank" rel="noopener noreferrer"' in html


def test_ordinary_external_link_keeps_existing_markup(node_available):
    html = _run_markdown_case("https://example.com/page")

    assert html == (
        '<a href="https://example.com/page" target="_blank" '
        'rel="noopener noreferrer">https://example.com/page</a>'
    )


def test_mermaid_fence_keeps_source_until_browser_render(node_available):
    html = _run_markdown_case(
        "```mermaid\nflowchart LR\n  A[Start] --> B[Done]\n```"
    )

    assert 'class="mermaid-container"' in html
    assert 'data-mermaid-state="pending"' in html
    assert 'class="mermaid-visual"' in html
    assert 'class="mermaid-source"' in html
    assert "flowchart LR" in html
    assert "A[Start] --&gt; B[Done]" in html
    assert '<pre class="mermaid"' not in html


def test_mermaid_is_self_hosted_and_strict():
    index = (_REPO / "static" / "index.html").read_text(encoding="utf-8")
    markdown = (_REPO / "static" / "js" / "markdown.js").read_text(encoding="utf-8")
    service_worker = (_REPO / "static" / "sw.js").read_text(encoding="utf-8")

    assert 'src="/static/lib/mermaid.min.js?v=11.17.2"' in index
    assert "cdn.jsdelivr.net/npm/mermaid" not in index
    assert "'/static/lib/mermaid.min.js?v=11.17.2'" in service_worker
    assert "securityLevel: 'strict'" in markdown
    assert "suppressErrorRendering: true" in markdown
    assert (_REPO / "static" / "lib" / "mermaid.min.js").is_file()
    assert (_REPO / "static" / "lib" / "mermaid.LICENSE.txt").is_file()


def test_inline_code_content_is_html_escaped(node_available):
    # Inline code is now extracted before the global escape pass, so it must be
    # escaped at extraction time (matching the fenced-code-block handling).
    html = _run_markdown_case("Render `<b>$1 & 'q'</b>` literally.")

    assert "<code>&lt;b&gt;$1 &amp; &#39;q&#39;&lt;/b&gt;</code>" in html
    assert "<b>" not in html


def test_currency_dollar_amounts_are_not_rendered_as_math(node_available):
    # "$5 to $10" used to pair the two dollar signs as inline-math delimiters
    # and render "5 to" through KaTeX. Pandoc-style rules now reject it: the
    # closing $ is preceded by a space and followed by a digit.
    html = _run_markdown_case(
        "The price rose from $5 to $10 overnight.", with_katex=True
    )

    assert 'class="katex"' not in html
    assert "$5" in html
    assert "$10" in html


def test_inline_math_still_renders_through_katex(node_available):
    html = _run_markdown_case("Pythagoras: $x^2 + y^2 = z^2$ holds.", with_katex=True)

    assert '<span class="katex" data-display="false">x^2 + y^2 = z^2</span>' in html
    assert "$" not in html


def test_display_math_still_renders_through_katex(node_available):
    html = _run_markdown_case("$$\\frac{a}{b}$$", with_katex=True)

    assert 'data-display="true"' in html
    assert "$$" not in html


def test_dotted_python_import_paths_are_not_autolinked(node_available):
    html = _run_markdown_case(
        "from imblearn.combine import SMOTETomek\n"
        "from sklearn.metrics import f1_score\n"
        "from sklearn.compose import ColumnTransformer\n\n"
        "See example.com/docs for normal domain autolinking."
    )

    assert "___ALLOWED_HTML_" not in html
    assert "imblearn.combine" in html
    assert "sklearn.metrics" in html
    assert "sklearn.compose" in html
    assert 'href="https://imblearn.com' not in html
    assert 'href="https://sklearn.me' not in html
    assert 'href="https://example.com/docs"' in html
