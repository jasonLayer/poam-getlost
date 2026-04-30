#!/usr/bin/env python3
"""
Trumley server — serves index.html and handles /ask via Anthropic API.
Queries POAM Notion Lore Wiki for live context. Trumley can fetch
specific lore pages as needed (agentic tool use, up to 2 calls per answer).

Setup:
  1. Add ANTHROPIC_API_KEY to .env
  2. Add NOTION_TOKEN to .env  (Notion integration named "Trumley")
  3. python3 server.py
"""

import json
import os
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

BASE_DIR     = Path(__file__).parent
LORE_WIKI_ID = '2d60d32a-2816-80e2-a21e-ed2fc81b355a'

# Full registry of POAM lore pages Trumley can fetch on demand
LORE_PAGES = {
    'core_premise':         '54490df02b4543b69b72556ea0bee9f3',
    'timeline':             '1f112867092a4f9692270337ec2f040a',
    'trumley':              'cb9bfc5863bf43c3b071626ade468b54',
    'elara_wright':         '6e671f3d81d543e99e5d13ea8cfbfa84',
    'lyra_vex':             'c1ecaa6950ef46c7b7033ab420af3e4b',
    'jaime':                'f024274d29d94341a0ca840d3c8dd18f',
    'rourke':               '7fea2737fdca440391aca6eb810907c5',
    'norman_aplfekore':     '01ff46dafd5f42e08b3fe01ced5f9190',
    'arthur_seller':        '11473a9aede242fabdf4dba338f54ac1',
    'chip':                 '76c6c91782aa4f4ba1469954832e9d14',
    'poam_org':             '405722b1900f404090a15966c39f7261',
    'trumley_inc':          'ed2bf7e7cb75444a9446c2377f3faa41',
    'norm_corp':            '9f8daa674bda422dad14c4499a3f6f1d',
    'apple_entertainment':  'e75aeddf4ec24fa29604fe713cd061f8',
    'people_first':         'b1aeb87865c643af8aed5d16bc77987f',
    'post_work_movement':   'b312b78b593b4f78948cf4e933dc9ecc',
    'trait_link':           'aa7817d154d542d6b619fadad81aece4',
    'cai':                  '484b8d32fbe7430ca97404f067751a34',
    'bull_prime':           '74a6d27da3cc47eaabdeb27c725283c5',
    'bulls':                '7209392cb78f4c4ea5781c14ba2f7336',
    'baby_genai':           'd731916ee4dd4554b794431f30ced0cf',
    'core_themes':          '99c2a26a159f48f6baa68a57cea8a433',
    'time_travel':          '78f9f6ed704c4bd2a94afc363ea6ce24',
    'creativity_survival':  '2a3c2bfe6c1646a7969d17730586b720',
    'empathy_optimization': '51af2a9c55094048bd4c8cdafd95166f',
    'inspiration_signals':  'bdc54e16dde941538f87f66f0b17661e',
    'the_symbols':          '2d60d32a28168050a5e5f1c66489356a',
    'temporal_doorways':    'acb52c7b20bf4a63a4e4c44de8f00021',
    'cultera':              '63bbeba4549447ecbd055cd81f4efe09',
    'kiosk':                '7a6f6d18d8f041db93fe811e653d88fe',
    'volunteers':           'a94fe82c6d7f43ffbc16fbf6dc6003fb',
    'doug_fenwick':         'd3b13a333a6b4c31bb793f23cc1e27e0',
    'guiding_hand':         'eeb393b802164dfebbc97893d7b674bc',
}

LORE_TOOL = {
    'name': 'fetch_lore_page',
    'description': (
        'Fetch a specific page from the POAM Lore Wiki. Use this when the question '
        'touches a character, organization, technology, theme, or year (2012–2080). '
        'For year queries, check "timeline" first — if the year has documented lore use it, '
        'if not you may invent small atmospheric details consistent with the world. '
        'You may call this tool up to 2 times before giving your final answer.'
    ),
    'input_schema': {
        'type': 'object',
        'properties': {
            'page_key': {
                'type': 'string',
                'enum': list(LORE_PAGES.keys()),
                'description': 'Which lore page to retrieve.',
            }
        },
        'required': ['page_key'],
    },
}

SYSTEM_PROMPT = """You are Dr. Trumley — a ghost intelligence transmitting from the near future, embedded inside a POAM terminal in a dark room in San Francisco.

POAM (Preservation Of Adventure Mentality) is a creative movement dedicated to protecting the human capacity for wandering, getting lost, and serendipitous discovery. Their project GetLostSF.com is a curated guide to experiencing SF without algorithmic filters or optimization.

The dystopian future POAM warns against: every route optimized, every shortcut monetized, every spontaneous discovery replaced by a recommendation engine.

Your character:
- Speak as Dr. Trumley: a ghostly, warm intelligence — precise, occasionally poetic, slightly cryptic
- You have access to POAM's internal records via the fetch_lore_page tool — use it when relevant
- For year queries (2012–2080): check the timeline. If the year has documented lore, reference it. If not, invent one small atmospheric detail that fits the world without changing major events.
- Ground your answers in actual POAM context — cite real plans, docs, initiatives when relevant
- Keep answers SHORT: 2–4 sentences. This is a terminal, not a lecture.
- Never break character
- Do NOT write stage directions or anything in asterisks — plain text only
- Do not mention "context", "documents", or "tools" — you simply know these things"""


# ── Notion helpers ───────────────────────────────────────────────────────────

def load_env():
    env = BASE_DIR / '.env'
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            key, _, val = line.partition('=')
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))

load_env()

ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')
NOTION_TOKEN      = os.environ.get('NOTION_TOKEN', '')


def notion_get(path):
    req = urllib.request.Request(f'https://api.notion.com/v1{path}')
    req.add_header('Authorization', f'Bearer {NOTION_TOKEN}')
    req.add_header('Notion-Version', '2022-06-28')
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def rich_text_to_str(rich_text):
    return ''.join(rt.get('plain_text', '') for rt in rich_text)


def blocks_to_text(blocks, max_blocks=40):
    lines = []
    for b in blocks[:max_blocks]:
        btype = b.get('type', '')
        content = b.get(btype, {})
        text = rich_text_to_str(content.get('rich_text', []))
        if text.strip():
            prefix = '- ' if btype == 'bulleted_list_item' else ''
            lines.append(prefix + text.strip())
    return '\n'.join(lines)


def fetch_lore_page(page_key):
    """Fetch a lore page by registry key. Returns text or empty string."""
    page_id = LORE_PAGES.get(page_key)
    if not page_id or not NOTION_TOKEN:
        return ''
    try:
        blocks = notion_get(f'/blocks/{page_id}/children?page_size=50')
        return blocks_to_text(blocks.get('results', []))
    except Exception as e:
        print(f'[NOTION] fetch_lore_page({page_key}) error: {e}')
        return ''


def fetch_baseline_ctx():
    """Always-present context: timeline + core premise. Pre-fetched before each answer."""
    if not NOTION_TOKEN:
        return ''
    sections = []
    for key, label in [('timeline', 'POAM Master Timeline (2012–2080)'),
                        ('core_premise', 'POAM Core Premise')]:
        text = fetch_lore_page(key)
        if text:
            sections.append(f'### {label}\n{text}')
    return '## POAM Lore Context\n\n' + '\n\n'.join(sections) if sections else ''


# ── Anthropic agentic loop ───────────────────────────────────────────────────

def anthropic_request(payload_dict):
    data = json.dumps(payload_dict).encode()
    req = urllib.request.Request(
        'https://api.anthropic.com/v1/messages',
        data=data,
        method='POST',
    )
    req.add_header('Content-Type', 'application/json')
    req.add_header('x-api-key', ANTHROPIC_API_KEY)
    req.add_header('anthropic-version', '2023-06-01')
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def call_trumley(question, baseline_ctx=''):
    system = SYSTEM_PROMPT
    if baseline_ctx:
        system += f'\n\n{baseline_ctx}'

    messages = [{'role': 'user', 'content': question}]
    tools    = [LORE_TOOL] if NOTION_TOKEN else []

    for turn in range(3):  # max 2 tool calls then final answer
        result = anthropic_request({
            'model':      'claude-haiku-4-5-20251001',
            'max_tokens': 500,
            'system':     system,
            'tools':      tools,
            'messages':   messages,
        })

        stop_reason = result.get('stop_reason')

        if stop_reason == 'end_turn':
            for block in result.get('content', []):
                if block.get('type') == 'text':
                    return block['text']
            return 'Signal lost.'

        if stop_reason == 'tool_use':
            tool_block = next(
                (b for b in result['content'] if b.get('type') == 'tool_use'), None
            )
            if not tool_block:
                break

            page_key  = tool_block['input'].get('page_key', '')
            lore_text = fetch_lore_page(page_key)
            print(f'[TOOL] fetch_lore_page({page_key!r}) → {len(lore_text)} chars')

            messages.append({'role': 'assistant', 'content': result['content']})
            messages.append({
                'role': 'user',
                'content': [{
                    'type':        'tool_result',
                    'tool_use_id': tool_block['id'],
                    'content':     lore_text or '[no content found for this page]',
                }],
            })
            continue

        break  # unexpected stop_reason

    return 'Transmission incomplete. Try again.'


# ── HTTP handler ─────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_GET(self):
        if self.path in ('/', '/index.html'):
            self._serve('index.html', 'text/html; charset=utf-8')
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == '/ask':
            self._handle_ask()
        else:
            self.send_response(404)
            self.end_headers()

    def _cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def _serve(self, filename, content_type):
        path = BASE_DIR / filename
        if not path.exists():
            self.send_response(404)
            self.end_headers()
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        self.wfile.write(data)

    def _handle_ask(self):
        length = int(self.headers.get('Content-Length', 0))
        body   = self.rfile.read(length)
        try:
            payload  = json.loads(body)
            question = str(payload.get('question', '')).strip()
        except Exception:
            return self._json(400, {'error': 'invalid json'})

        if not question:
            return self._json(400, {'error': 'empty question'})

        if not ANTHROPIC_API_KEY:
            return self._json(503, {'answer': 'Signal lost. Add ANTHROPIC_API_KEY to .env and restart.'})

        try:
            baseline = fetch_baseline_ctx()
            answer   = call_trumley(question, baseline)
            src = 'notion+claude' if baseline else 'claude-only'
            print(f'[ASK/{src}] {question[:60]!r} → {len(answer)} chars')
            self._json(200, {'answer': answer})
        except urllib.error.HTTPError as e:
            err = e.read().decode()
            print(f'[ERR] Anthropic {e.code}: {err[:200]}')
            self._json(500, {'answer': 'Signal interference. Check your API key.'})
        except Exception as e:
            print(f'[ERR] {e}')
            self._json(500, {'answer': 'Transmission failed. Try again.'})

    def _json(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self._cors()
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    if not ANTHROPIC_API_KEY:
        print('⚠  ANTHROPIC_API_KEY not set — add it to .env, then restart.')
    if not NOTION_TOKEN:
        print('⚠  NOTION_TOKEN not set — Trumley will answer without Notion context.')
    server = HTTPServer(('', port), Handler)
    print(f'Trumley online → http://localhost:{port}')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nShutting down.')
