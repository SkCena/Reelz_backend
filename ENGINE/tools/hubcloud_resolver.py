"""
HubCloud resolver for Indian mirror sites (4KHDHub, VegaMovies, etc.)
Ported from Streamline's hubcloud.js
"""
from __future__ import annotations

import base64
import re
import asyncio
from typing import Optional
from urllib.parse import urljoin

from ENGINE.tools.http import get_client, UA


def _rot13(s: str) -> str:
    result = []
    for c in s:
        if 'a' <= c <= 'z':
            result.append(chr((ord(c) - ord('a') + 13) % 26 + ord('a')))
        elif 'A' <= c <= 'Z':
            result.append(chr((ord(c) - ord('A') + 13) % 26 + ord('A')))
        else:
            result.append(c)
    return ''.join(result)


def _b64_decode_utf8(s: str) -> str:
    s = s.strip()
    missing = len(s) % 4
    if missing:
        s += '=' * (4 - missing)
    return base64.b64decode(s).decode('utf-8', errors='ignore')


def _get_base_url(url: str) -> str:
    try:
        from urllib.parse import urlparse
        p = urlparse(url)
        return f"{p.scheme}://{p.netloc}"
    except:
        return url


async def get_redirect_links(url: str) -> str:
    """Port of getRedirectLinks() - decodes the redirect chain."""
    try:
        client = await get_client()
        r = await client.get(url, headers={'User-Agent': UA}, timeout=15, follow_redirects=True)
        doc = r.text

        pattern = r"s\('o','([A-Za-z0-9+/=]+)'|ck\('_wp_http_\d+','([^']+)'"
        combined = ""
        for m in re.finditer(pattern, doc):
            combined += m.group(1) or m.group(2) or ""

        if not combined:
            return ""

        decoded = _b64_decode_utf8(_rot13(_b64_decode_utf8(_b64_decode_utf8(combined))))

        import json
        try:
            obj = json.loads(decoded)
        except:
            return ""

        encoded_url = _b64_decode_utf8(obj.get('o', '')).strip()
        data = base64.b64encode((obj.get('data', '') or '').encode()).decode().strip()
        blog_url = (obj.get('blog_url', '') or '').strip()

        direct = ""
        if blog_url and data:
            try:
                r2 = await client.get(f"{blog_url}?re={data}", headers={'User-Agent': UA}, timeout=15)
                direct = r2.text.strip()
            except:
                pass

        return encoded_url or direct
    except:
        return ""


async def resolve_hubcloud(url: str, referer: str = "") -> list[dict]:
    """
    Port of HubCloud.getUrl() - resolves a hubcloud/vcloud page to direct links.
    Returns list of {'url': ..., 'server': ..., 'quality': ...}
    """
    out = []
    try:
        client = await get_client()
        base_url = _get_base_url(url)

        # Fetch the hubcloud page
        headers = {'User-Agent': UA}
        if referer:
            headers['Referer'] = referer

        r = await client.get(url, headers=headers, timeout=20, follow_redirects=True)
        doc = r.text

        from bs4 import BeautifulSoup
        soup = BeautifulSoup(doc, 'html.parser')

        # Extract the inner link
        link = ""
        if '/video/' in url:
            a = soup.select_one('div.vd > center > a')
            if a:
                link = (a.get('href') or '').strip()
        else:
            # Find var url in scripts
            script_text = ""
            for s in soup.find_all('script'):
                t = s.string or ''
                if 'url' in t and len(t) < 20000:
                    script_text += t + "\n"
            script_tag = script_text or doc

            if 'vcloud' in url:
                # Double atob
                m = re.search(r'var\s+url\s*=\s*atob\s*\(\s*atob\s*\(\s*[\'"]([^\'"]+)[\'"]\s*\)\s*\)', script_tag)
                if m:
                    try:
                        link = _b64_decode_utf8(_b64_decode_utf8(m.group(1)))
                    except:
                        pass
            else:
                m = re.search(r"var url = '([^']*)'", script_tag)
                if m:
                    link = m.group(1)

        if not link:
            return out

        if not link.startswith('https://'):
            link = base_url + link

        # Fetch the download page
        r2 = await client.get(link, headers={'User-Agent': UA, 'Referer': url}, timeout=20, follow_redirects=True)
        page2 = r2.text
        soup2 = BeautifulSoup(page2, 'html.parser')

        header = ""
        h = soup2.select_one('div.card-header')
        if h:
            header = h.get_text().strip()

        # Find download buttons
        btns = soup2.select('h2 a.btn')
        for btn in btns:
            href = btn.get('href') or ''
            text = btn.get_text() or ''
            if not href:
                continue

            server = None
            if re.search(r'FSL Server|FSLv2|Mega Server|Download File', text):
                if 'FSLv2' in text:
                    server = 'FSLv2'
                elif 'Mega' in text:
                    server = 'Mega'
                elif 'Download File' in text:
                    server = 'Download'
                else:
                    server = 'FSL'
                # FSL links are often direct R2/storage URLs - use directly
                if 'r2.cloudflarestorage' in href or re.search(r'\.(mp4|mkv|m3u8)(\?|$)', href, re.I):
                    out.append({
                        'url': href,
                        'server': server,
                        'quality': _parse_quality(header or href)
                    })
                    continue
            elif 'pixeldra' in href:
                # Pixeldrain
                m = re.search(r'var\s+pxl\s*=\s*["\']([^"\']+)["\']', page2)
                if m:
                    pxl = m.group(1)
                    b = _get_base_url(pxl)
                    if '/download' in pxl.lower():
                        direct_url = pxl
                    else:
                        direct_url = f"{b}/api/file/{pxl.split('/')[-1]}?download"
                    out.append({'url': direct_url, 'server': 'Pixeldrain', 'quality': _parse_quality(header)})
                continue
            elif 'Server : 10Gbps' in text:
                # 10Gbps often returns 500 or hangs - skip
                continue
            elif 'Buzz Server' in text:
                # Need to fetch buzz page for download-btn
                try:
                    rb = await client.get(href, headers={'User-Agent': UA}, timeout=15)
                    soupb = BeautifulSoup(rb.text, 'html.parser')
                    dl = soupb.select_one('.download-btn')
                    if dl and dl.get('href'):
                        out.append({
                            'url': _get_base_url(href) + dl.get('href'),
                            'server': 'Buzz',
                            'quality': _parse_quality(header)
                        })
                except:
                    pass
                continue
            elif 'Watch Online' in text:
                # Watch Online often has direct stream URL
                # Skip - these are often slow/unreliable
                continue

            if server:
                # Resolve the final URL
                final_url = await _resolve_final(href, link)
                if final_url:
                    # For R2/storage URLs, skip probe (they're direct)
                    if 'r2.cloudflarestorage' in final_url:
                        out.append({
                            'url': final_url,
                            'server': server,
                            'quality': _parse_quality(header or final_url)
                        })
                    elif await _probe_ok(final_url, link):
                        out.append({
                            'url': final_url,
                            'server': server,
                            'quality': _parse_quality(header or final_url)
                        })

    except Exception as e:
        pass

    return out


async def _resolve_final(url: str, referer: str) -> str:
    """Follow redirects to get final URL."""
    try:
        client = await get_client()
        headers = {'User-Agent': UA, 'Referer': referer, 'Range': 'bytes=0-0'}
        cur = url
        for _ in range(7):
            r = await client.get(cur, headers=headers, timeout=15, follow_redirects=False)
            if r.status_code < 300 or r.status_code > 399:
                break
            loc = r.headers.get('location', '')
            if not loc:
                break
            cur = urljoin(cur, loc)

        if cur != url and 'link=' in cur:
            cur = cur.split('link=')[1]
        return cur
    except:
        return url


async def _probe_ok(url: str, referer: str) -> bool:
    """Check if URL is playable with 1-byte probe."""
    try:
        client = await get_client()
        headers = {'User-Agent': UA, 'Referer': referer, 'Range': 'bytes=0-0'}
        r = await client.get(url, headers=headers, timeout=15, follow_redirects=True)
        if r.status_code == 206:
            return True
        if r.status_code == 200:
            ct = r.headers.get('content-type', '')
            if re.search(r'video|octet-stream|matroska|mp4|mpegurl|m3u8', ct, re.I):
                return True
        return False
    except:
        return False


def _parse_quality(text: str) -> str:
    """Extract quality from text."""
    text = text.lower()
    if '2160p' in text or '4k' in text:
        return '2160p'
    if '1080p' in text:
        return '1080p'
    if '720p' in text:
        return '720p'
    if '480p' in text:
        return '480p'
    return 'HD'


async def resolve_4khdhub_movie(domain: str, title: str, year: int = None) -> list[dict]:
    """
    Full 4KHDHub movie resolution: search -> detail -> hubcloud -> streams.
    Returns list of {'url': ..., 'server': ..., 'quality': ...}
    """
    out = []
    try:
        client = await get_client()
        from bs4 import BeautifulSoup
        from urllib.parse import quote

        # Search
        r = await client.get(f"{domain}/?s={quote(title)}", headers={'User-Agent': UA}, timeout=15)
        soup = BeautifulSoup(r.text, 'html.parser')

        # Find matching card
        cards = soup.select('div.card-grid > a.movie-card')
        matched = None
        norm_title = title.lower().strip()
        year_str = str(year) if year else None

        for card in cards:
            c = card.get_text('', strip=True).lower()
            if norm_title in c and (year_str is None or year_str in c):
                matched = card
                break
        if not matched:
            for card in cards:
                if norm_title in card.get_text('', strip=True).lower():
                    matched = card
                    break
        if not matched:
            return out

        link = matched.get('href') or ''
        detail_url = link if link.startswith('http') else f"{domain}{link}"

        # Detail page
        r2 = await client.get(detail_url, headers={'User-Agent': UA}, timeout=15)
        soup2 = BeautifulSoup(r2.text, 'html.parser')

        # Get download links
        hrefs = set()
        for a in soup2.select('div.download-item a'):
            h = a.get('href') or ''
            if h:
                hrefs.add(h)

        # Resolve each through HubCloud
        for href in list(hrefs)[:5]:  # Limit to 5
            try:
                # Step 1: Decode redirect
                hubcloud_url = await get_redirect_links(href)
                if not hubcloud_url:
                    # Try direct
                    hubcloud_url = href

                # Step 2: Resolve HubCloud
                if 'hubcloud' in hubcloud_url or 'vcloud' in hubcloud_url:
                    streams = await resolve_hubcloud(hubcloud_url, detail_url)
                    out.extend(streams)
                elif re.search(r'\.(mp4|mkv|m3u8)(\?|$)', hubcloud_url, re.I):
                    out.append({
                        'url': hubcloud_url,
                        'server': 'Direct',
                        'quality': _parse_quality(hubcloud_url)
                    })
            except:
                continue

    except Exception as e:
        pass

    return out
