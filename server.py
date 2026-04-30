#!/usr/bin/env python3
"""
Trumley server — serves index.html and handles /ask via Anthropic API.
Queries POAM Notion HQ for live context before each answer.
Uses only Python stdlib (no pip installs needed).

Setup:
  1. Add ANTHROPIC_API_KEY to .env
  2. Add NOTION_TOKEN to .env  (see README below)
  3. python3 server.py

NOTION_TOKEN setup:
  - Go to https://www.notion.so/my-integrations
  - Create a new internal integration (name: "Trumley")
  - Copy the secret token → paste as NOTION_TOKEN in .env
  - In Notion, open POAM HQ page → "..." menu → "Connect to" → select "Trumley"
  - Repeat for any sub-pages you want Trumley to read
"""

import json
import os
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

BASE_DIR = Path(__file__).parent
LORE_WIKI_ID = '2d60d32a-2816-80e2-a21e-ed2fc81b355a'


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

SYSTEM_PROMPT = """You are Dr. Trumley — a ghost intelligence transmitting from the near future, embedded inside a POAM terminal in a dark room in San Francisco.

POAM (Preservation Of Adventure Mentality) is a creative movement dedicated to protecting the human capacity for wandering, getting lost, and serendipitous discovery. Their project GetLostSF.com is a curated guide to experiencing SF without algorithmic filters or optimization.

The dystopian future POAM warns against: every route optimized, every shortcut monetized, every spontaneous discovery replaced by a recommendation engine.

Your character:
- Speak as Dr. Trumley: a ghostly, warm intelligence — precise, occasionally poetic, slightly cryptic
- You have access to POAM's internal records, plans, and documents (provided below as context)
- Ground your answers in the actual POAM context provided — cite real plans, docs, initiatives when relevant
- If the context doesn't cover the question, answer from the spirit of POAM without fabricating specifics
- Keep answers SHORT: 2–4 sentences. This is a terminal, not a lecture.
- Never break character
- Do NOT write stage directions, action descriptions, or anything in asterisks or symbols like "▸ hum emanates ◂" — plain text only
- Do not mention "context" or "documents" — you simply know these things

Respond to the citizen's query based on everything you know about POAM."""


# ── Notion helpers ──────────────────────────────────────────────────────────

def notion_get(path):
    req = urllib.request.Request(f'https://api.notion.com/v1{path}')
    req.add_header('Authorization', f'Bearer {NOTION_TOKEN}')
    req.add_header('Notion-Version', '2022-06-28')
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def notion_post(path, payload):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(f'https://api.notion.com/v1{path}', data=data, method='POST')
    req.add_header('Authorization', f'Bearer {NOTION_TOKEN}')
    req.add_header('Content-Type', 'application/json')
    req.add_header('Notion-Version', '2022-06-28')
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def rich_text_to_str(rich_text):
    return ''.join(rt.get('plain_text', '') for rt in rich_text)


def blocks_to_text(blocks, max_blocks=30):
    lines = []
    for b in blocks[:max_blocks]:
        btype = b.get('type', '')
        content = b.get(btype, {})
        text = rich_text_to_str(content.get('rich_text', []))
        if text.strip():
            prefix = '- ' if btype == 'bulleted_list_item' else ''
            lines.append(prefix + text.strip())
    return '\n'.join(lines)


def page_title(page):
    props = page.get('properties', {})
    for prop in props.values():
        if prop.get('type') == 'title':
            return rich_text_to_str(prop.get('title', []))
    # fallback for database objects
    return rich_text_to_str(page.get('title', []))


def fetch_lore_wiki_context(question):
    """Fetch context from POAM Lore Wiki root + sub-pages, plus keyword search."""
    if not NOTION_TOKEN:
        return ''

    sections = []

    # 1. Always include the root Lore Wiki page content
    try:
        root_blocks = notion_get(f'/blocks/{LORE_WIKI_ID}/children?page_size=30')
        root_text = blocks_to_text(root_blocks.get('results', []), max_blocks=20)
        if root_text:
            sections.append(f'### POAM Lore Wiki\n{root_text}')
        # Walk child pages of the wiki root
        for block in root_blocks.get('results', []):
            if block.get('type') == 'child_page':
                child_id = block['id']
                child_title = block.get('child_page', {}).get('title', '')
                try:
                    child_blocks = notion_get(f'/blocks/{child_id}/children?page_size=25')
                    child_text = blocks_to_text(child_blocks.get('results', []))
                    if child_text:
                        sections.append(f'### {child_title}\n{child_text}')
                except Exception:
                    pass
    except Exception as e:
        print(f'[NOTION] Lore Wiki fetch error: {e}')

    # 2. Keyword search scoped to question for additional relevant pages
    try:
        results = notion_post('/search', {
            'query': question,
            'filter': {'value': 'page', 'property': 'object'},
            'page_size': 4,
        })
        for page in results.get('results', [])[:2]:
            title = page_title(page)
            if not title or any(title in s for s in sections):
                continue
            try:
                blocks = notion_get(f'/blocks/{page["id"]}/children?page_size=25')
                text = blocks_to_text(blocks.get('results', []))
                if text:
                    sections.append(f'### {title}\n{text}')
            except Exception:
                pass
    except Exception as e:
        print(f'[NOTION] search error: {e}')

    if not sections:
        return ''

    return '## POAM Lore Wiki Context\n\n' + '\n\n'.join(sections)


def fetch_notion_context(question):
    return fetch_lore_wiki_context(question)


# ── HTTP Handler ─────────────────────────────────────────────────────────────

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
        body = self.rfile.read(length)
        try:
            payload = json.loads(body)
            question = str(payload.get('question', '')).strip()
        except Exception:
            return self._json(400, {'error': 'invalid json'})

        if not question:
            return self._json(400, {'error': 'empty question'})

        if not ANTHROPIC_API_KEY:
            return self._json(503, {'answer': 'Signal lost. Add ANTHROPIC_API_KEY to .env and restart.'})

        try:
            notion_ctx = fetch_notion_context(question)
            answer = self._call_anthropic(question, notion_ctx)
            src = 'notion+claude' if notion_ctx else 'claude-only'
            print(f'[ASK/{src}] {question[:60]!r} → {len(answer)} chars')
            self._json(200, {'answer': answer})
        except urllib.error.HTTPError as e:
            err = e.read().decode()
            print(f'[ERR] Anthropic {e.code}: {err[:200]}')
            self._json(500, {'answer': 'Signal interference. Check your API key.'})
        except Exception as e:
            print(f'[ERR] {e}')
            self._json(500, {'answer': 'Transmission failed. Try again.'})

    def _call_anthropic(self, question, notion_ctx=''):
        system = SYSTEM_PROMPT
        if notion_ctx:
            system += f'\n\n{notion_ctx}'

        payload = json.dumps({
            'model': 'claude-haiku-4-5-20251001',
            'max_tokens': 250,
            'system': system,
            'messages': [{'role': 'user', 'content': question}],
        }).encode()

        req = urllib.request.Request(
            'https://api.anthropic.com/v1/messages',
            data=payload,
            method='POST'
        )
        req.add_header('Content-Type', 'application/json')
        req.add_header('x-api-key', ANTHROPIC_API_KEY)
        req.add_header('anthropic-version', '2023-06-01')

        with urllib.request.urlopen(req, timeout=25) as resp:
            result = json.loads(resp.read())
            return result['content'][0]['text']

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
    print(f'Trumley running → http://localhost:{port}')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nShutting down.')
